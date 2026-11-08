"""Unit and integration tests for shard router."""

import uuid
from app.core.shard_router import (
    RouterSettings,
    ShardRouter,
    ShardSpec,
    make_id,
    shard_id_of,
    NS_EMAIL,
    NS_GOOGLE,
    merge_page,
)
from app.db.base import Base
from app.db.models import Listing, User
from sqlalchemy import create_engine


def test_zero_lookup_byte_routing():
    for sid in range(6):
        uid = make_id(sid)
        assert shard_id_of(uid) == sid
        assert shard_id_of(str(uid)) == sid


def test_merge_page():
    # 3 shards each with sorted numbers
    s0 = [10, 8, 4]
    s1 = [12, 7, 5]
    s2 = [11, 9, 3]
    merged = merge_page([s0, s1, s2], key=lambda x: x, limit=4, offset=2, reverse=True)
    # Global sorted: [12, 11, 10, 9, 8, 7, 5, 4, 3]
    # offset 2, limit 4 -> [10, 9, 8, 7]
    assert merged == [10, 9, 8, 7]


def test_router_with_sqlite_shards(tmp_path):
    # Test topology with 3 shards
    db0 = f"sqlite:///{tmp_path}/shard0.db"
    db1 = f"sqlite:///{tmp_path}/shard1.db"
    db2 = f"sqlite:///{tmp_path}/shard2.db"

    specs = [
        ShardSpec(shard_id=0, url=db0, name="shard-0"),
        ShardSpec(shard_id=1, url=db1, name="shard-1"),
        ShardSpec(shard_id=2, url=db2, name="shard-2"),
    ]

    settings = RouterSettings(
        stats_interval=0,  # disable background thread
        directory_shards=(0, 1),
    )

    def engine_factory(spec, _st):
        return create_engine(spec.url)

    def dummy_stats(_shard):
        from app.core.shard_router import ShardStats
        return ShardStats(db_bytes=1000, approx_rows=10)

    router = ShardRouter(
        specs,
        settings,
        engine_factory=engine_factory,
        stats_provider=dummy_stats,
    )
    router.start(background=False)
    router.create_all(Base.metadata)

    # 1. Directory reserve & lookup
    u_alloc = router.allocate()
    assert u_alloc.shard_id in (0, 1, 2)
    assert shard_id_of(u_alloc.entity_id) == u_alloc.shard_id

    email = "test@example.com"
    reserved = router.directory.reserve(NS_EMAIL, email, u_alloc.entity_id)
    assert reserved is True

    # Duplicate reserve fails
    another_id = make_id(1)
    reserved2 = router.directory.reserve(NS_EMAIL, email, another_id)
    assert reserved2 is False

    lookup_id = router.directory.lookup(NS_EMAIL, email)
    assert lookup_id == u_alloc.entity_id

    # 2. Placement colocation
    l_alloc = router.allocate(near=u_alloc.entity_id)
    listing_id = l_alloc.entity_id
    bid_id = router.colocated_id(listing_id)
    assert shard_id_of(bid_id) == shard_id_of(listing_id)

    # 3. Unit of work
    with router.unit_of_work() as uow:
        user = User(
            id=u_alloc.entity_id,
            email=email,
            password_hash="fake",
            role="seller",
            display_name="Tester",
        )
        uow.for_shard(u_alloc.shard_id).add(user)
        uow.commit()

    # 4. Cross-shard load_by_ids
    loaded = router.load_by_ids(User, [u_alloc.entity_id])
    assert u_alloc.entity_id in loaded
    assert loaded[u_alloc.entity_id].email == email

    router.close()
