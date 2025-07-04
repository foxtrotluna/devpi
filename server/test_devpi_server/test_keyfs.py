import contextlib
import py
import pytest
from devpi_server.mythread import ThreadPool

from devpi_server.keyfs import KeyFS, Transaction
from devpi_server.readonly import is_deeply_readonly

notransaction = pytest.mark.notransaction


@pytest.fixture
def keyfs(gen_path, pool, storage):
    keyfs = KeyFS(gen_path(), storage)
    pool.register(keyfs.notifier)
    yield keyfs


@pytest.fixture(params=["direct", "a/b/c"])
def key(request):
    return request.param


@pytest.fixture
def pool():
    pool = ThreadPool()
    try:
        yield pool
    finally:
        pool.kill()


class TestKeyFS:
    def test_get_non_existent(self, keyfs):
        key = keyfs.add_key("NAME", "somekey", dict)
        pytest.raises(KeyError, lambda: keyfs.tx.get_value_at(key, 0))

    @notransaction
    def test_delete_non_existent(self, keyfs, key):
        k = keyfs.add_key("NAME", key, bytes)
        with keyfs.write_transaction():
            assert not k.exists()
            k.delete()
            assert not k.exists()
        with keyfs.read_transaction():
            assert not k.exists()
        with keyfs._storage.get_connection() as conn:
            # there should be no changelog entry
            assert conn.last_changelog_serial == -1

    @notransaction
    def test_keyfs_readonly(self, storage, tmpdir):
        keyfs = KeyFS(tmpdir, storage, readonly=True)
        with pytest.raises(keyfs.ReadOnly):
            with keyfs.write_transaction():
                pass
        assert not hasattr(keyfs, "tx")
        with pytest.raises(keyfs.ReadOnly):
            with keyfs.read_transaction():
                keyfs.restart_as_write_transaction()
        with pytest.raises(keyfs.ReadOnly):
            keyfs.begin_transaction_in_thread(write=True)
        with keyfs.read_transaction():
            pass

    @pytest.mark.writetransaction
    @pytest.mark.parametrize("val", [b"", b"val"])
    def test_get_set_del_exists(self, keyfs, key, val):
        k = keyfs.add_key("NAME", key, bytes)
        assert not k.exists()
        k.set(val)
        assert k.exists()
        newval = k.get()
        assert val == newval
        assert isinstance(newval, type(val))
        k.delete()
        assert not k.exists()
        keyfs.commit_transaction_in_thread()
        with keyfs.read_transaction():
            assert not k.exists()

    def test_no_slashkey(self, keyfs):
        pkey = keyfs.add_key("NAME", "{hello}", dict)
        with pytest.raises(ValueError):
            pkey(hello="this/that")

    @notransaction
    def test_remove_dict_key(self, keyfs):
        key = keyfs.add_key("NAME", "somekey", dict)
        with keyfs.write_transaction() as tx:
            tx.set(key, {u'foo': u'bar', u'ham': u'egg'})
        with keyfs.write_transaction() as tx:
            tx.set(key, {u'foo': u'bar'})
        with keyfs.read_transaction() as tx:
            assert tx.at_serial == 1
            assert tx.get(key) == {u'foo': u'bar'}
        with keyfs.read_transaction(at_serial=0) as tx:
            assert tx.at_serial == 0
            assert tx.get(key) == {u'foo': u'bar', u'ham': u'egg'}

    @notransaction
    def test_remove_set_item(self, keyfs):
        key = keyfs.add_key("NAME", "somekey", set)
        with keyfs.write_transaction() as tx:
            tx.set(key, {'bar', 'egg'})
        with keyfs.write_transaction() as tx:
            tx.set(key, {'bar'})
        with keyfs.read_transaction() as tx:
            assert tx.at_serial == 1
            assert tx.get(key) == {'bar'}
        with keyfs.read_transaction(at_serial=0) as tx:
            assert tx.at_serial == 0
            assert tx.get(key) == {'bar', 'egg'}

    @notransaction
    def test_double_set(self, keyfs):
        key = keyfs.add_key("NAME", "somekey", dict)
        with keyfs.write_transaction() as tx:
            tx.set(key, {u'foo': u'bar', u'ham': u'egg'})
        with keyfs.write_transaction() as tx:
            # set different value
            tx.set(key, {})
            # set to same value again in same transaction
            tx.set(key, {u'foo': u'bar', u'ham': u'egg'})
        with keyfs.read_transaction() as tx:
            # the serial shouldn't have increased
            assert tx.at_serial == 0
            assert tx.get(key) == {u'foo': u'bar', u'ham': u'egg'}

    @notransaction
    @pytest.mark.parametrize("before,after", [
        ({u'a': 1}, {u'b': 2}),
        (set([3]), set([4])),
        (5, 6)])
    def test_delete_and_readd(self, keyfs, before, after):
        key = keyfs.add_key("NAME", "somekey", type(before))
        with keyfs.write_transaction() as tx:
            tx.set(key, before)
        with keyfs.write_transaction() as tx:
            tx.delete(key)
        with keyfs.write_transaction() as tx:
            tx.set(key, after)
        with keyfs.read_transaction() as tx:
            assert tx.get(key) == after

    @notransaction
    def test_not_exists_cached(self, keyfs, monkeypatch):
        key = keyfs.add_key("NAME", "somekey", dict)
        with keyfs.read_transaction() as tx:
            assert key not in tx.cache
            assert key not in tx._original
            assert not tx.exists(key)
            assert key not in tx.cache
            assert key in tx._original
            # make get_value_at fail if it is called
            monkeypatch.setattr(tx, "get_value_at", lambda k, s: 0 / 0)
            assert not tx.exists(key)

    @notransaction
    def test_dirty_exists(self, keyfs):
        key = keyfs.add_key("NAME", "somekey", dict)
        with keyfs.write_transaction() as tx:
            assert not tx.exists(key)
            tx.set(key, {})
            assert tx.exists(key)


class TestGetKey:
    def test_typed_keys(self, keyfs):
        key = keyfs.add_key("NAME", "hello", dict)
        assert key == keyfs.get_key("NAME")
        assert key.name == "NAME"

    def test_pattern_key(self, keyfs):
        pkey = keyfs.add_key("NAME", "{hello}/{this}", dict)
        found_key = keyfs.get_key("NAME")
        assert found_key == pkey
        assert pkey.extract_params("cat/dog") == dict(hello="cat", this="dog")
        assert pkey.extract_params("cat") == {}
        assert pkey.name == "NAME"
        key = pkey(hello="cat", this="dog")
        assert key.name == "NAME"


@pytest.mark.parametrize(("type", "val"),
        [(dict, {1:2}),
         (set, set([1,2])),
         (int, 3),
         (tuple, (3,4)),
         (str, "hello")])
class Test_addkey_combinations:
    def test_addkey(self, keyfs, key, type, val):
        attr = keyfs.add_key("NAME", key, type)
        assert not attr.exists()
        assert attr.get() == type()
        assert not attr.exists()
        keyfs.restart_as_write_transaction()
        attr.set(val)
        assert attr.exists()
        assert attr.get() == val
        attr.delete()
        assert not attr.exists()
        assert attr.get() == type()

    def test_addkey_param(self, keyfs, type, val):
        pattr = keyfs.add_key("NAME", "hello/{some}", type)
        attr = pattr(some="this")
        assert not attr.exists()
        assert attr.get() == type()
        assert not attr.exists()
        keyfs.restart_as_write_transaction()
        attr.set(val)
        assert attr.exists()
        assert attr.get() == val

        attr2 = pattr(some="that")
        assert not attr2.exists()
        attr.delete()
        assert not attr.exists()
        assert attr.get() == type()

    def test_addkey_unicode(self, keyfs, type, val):
        pattr = keyfs.add_key("NAME", "hello/{some}", type)
        attr = pattr(some=b'\xe4'.decode("latin1"))
        assert not attr.exists()
        assert attr.get() == type()
        assert not attr.exists()
        keyfs.restart_as_write_transaction()
        attr.set(val)
        assert attr.exists()
        assert attr.get() == val


class TestKey:
    def test_addkey_type_mismatch(self, keyfs):
        dictkey = keyfs.add_key("NAME1", "some", dict)
        pytest.raises(TypeError, lambda: dictkey.set("hello"))
        dictkey = keyfs.add_key("NAME2", "{that}/some", dict)
        pytest.raises(TypeError, lambda: dictkey(that="t").set("hello"))

    def test_addkey_registered(self, keyfs):
        key1 = keyfs.add_key("SOME1", "some1", dict)
        key2 = keyfs.add_key("SOME2", "some2", list)
        assert len(keyfs._keys) == 2
        assert keyfs.get_key("SOME1") == key1
        assert keyfs.get_key("SOME2") == key2

    def test_readonly(self, keyfs):
        key1 = keyfs.add_key("NAME", "some1", dict)
        keyfs.restart_as_write_transaction()
        with key1.update() as d:
            d[1] = l = [1,2,3]
        assert key1.get()[1] == l
        keyfs.commit_transaction_in_thread()
        with keyfs.read_transaction():
            assert key1.get()[1] == l
            with pytest.raises(TypeError):
                key1.get()[13] = "something"

    def test_write_then_readonly(self, keyfs):
        key1 = keyfs.add_key("NAME", "some1", dict)
        keyfs.restart_as_write_transaction()
        with key1.update() as d:
            d[1] = [1,2,3]

        # if we get it new, we still get a readonly view
        assert is_deeply_readonly(key1.get())

        # if we get it new in write mode, we get a new copy
        d2 = key1.get_mutable()
        assert d2 == d
        d2[3] = 4
        assert d2 != d

    def test_update(self, keyfs):
        key1 = keyfs.add_key("NAME1", "some1", dict)
        key2 = keyfs.add_key("NAME2", "some2", list)
        keyfs.restart_as_write_transaction()
        with key1.update() as d:
            with key2.update() as l:
                l.append(1)
                d["hello"] = l
        assert key1.get()["hello"] == l

    def test_get_inplace(self, keyfs):
        key1 = keyfs.add_key("NAME", "some1", dict)
        keyfs.restart_as_write_transaction()
        key1.set({1: 2})
        with contextlib.suppress(ValueError), key1.update() as d:
            d["hello"] = "world"
            raise ValueError
        assert key1.get() == {1: 2}

    def test_filestore(self, keyfs):
        key1 = keyfs.add_key("NAME", "hello", bytes)
        keyfs.restart_as_write_transaction()
        key1.set(b"hello")
        assert key1.get() == b"hello"
        keyfs.commit_transaction_in_thread()


@notransaction
@pytest.mark.parametrize(("type", "val"),
        [(dict, {1:2}),
         (set, set([1,2])),
         (int, 3),
         (tuple, (3,4)),
         (str, "hello")])
def test_trans_get_not_modify(keyfs, type, val, monkeypatch):
    attr = keyfs.add_key("NAME", "hello", type)
    with keyfs.write_transaction():
        attr.set(val)
    with keyfs.read_transaction():
        assert attr.get() == val
    # make sure keyfs doesn't write during the transaction and its commit
    orig_write = py.path.local.write

    def write_checker(path, content):
        assert not path.endswith(attr.relpath)
        orig_write(path, content)

    monkeypatch.setattr(py.path.local, "write", write_checker)
    with keyfs.read_transaction():
        x = attr.get()
    assert x == val


@notransaction
class TestTransactionIsolation:
    def test_cannot_write_on_read_trans(self, keyfs):
        key = keyfs.add_key("hello", "hello", dict)
        tx_1 = Transaction(keyfs)
        with pytest.raises(keyfs.ReadOnly):
            tx_1.set(key, {})
        with pytest.raises(keyfs.ReadOnly):
            tx_1.delete(key)

    def test_serialized_writing(self, TimeoutQueue, keyfs, storage_info):
        if "sqlite" not in storage_info["name"]:
            pytest.skip("The test is only relevant for sqlite based storages.")
        import threading
        q1 = TimeoutQueue()
        q2 = TimeoutQueue()

        def trans1():
            with keyfs.write_transaction():
                q1.put("write1")
                assert q2.get() == "1"
                q1.put("write1b")

        def trans2():
            with keyfs.write_transaction():
                q1.put("write2")

        t1 = threading.Thread(target=trans1)
        NUM = 3
        other = [threading.Thread(target=trans2) for i in range(NUM)]
        t1.start()
        assert q1.get() == "write1"
        for x in other:
            x.start()
        q2.put("1")
        assert q1.get() == "write1b"
        for i in range(NUM):
            assert q1.get() == "write2"

    def test_reading_while_writing(self, TimeoutQueue, keyfs):
        import threading
        q1 = TimeoutQueue()
        q2 = TimeoutQueue()
        q3 = TimeoutQueue()

        def trans1():
            with keyfs.write_transaction():
                q1.put("write1")
                assert q2.get() == "1"
                q1.put("write1b")

        def trans2():
            with keyfs.read_transaction():
                q3.put("read")

        t1 = threading.Thread(target=trans1)
        NUM = 3
        other = [threading.Thread(target=trans2) for i in range(NUM)]
        t1.start()
        assert q1.get() == "write1"
        for x in other:
            x.start()
        for i in range(NUM):
            assert q3.get() == "read"

        q2.put("1")
        assert q1.get() == "write1b"

    def test_concurrent_tx_sees_original_value_on_write(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        tx_1 = Transaction(keyfs, write=True)
        tx_2 = Transaction(keyfs)
        ser = tx_1.conn.last_changelog_serial + 1
        tx_1.set(D, {1:1})
        tx1_serial = tx_1.commit()
        assert tx1_serial == ser
        assert tx_2.at_serial == ser - 1
        with keyfs._storage.get_connection() as conn:
            assert conn.last_changelog_serial == ser
        assert D not in tx_2.cache and D not in tx_2.dirty
        assert tx_2.get(D) == {}

    def test_concurrent_tx_sees_original_value_on_delete(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.write_transaction():
            D.set({1:2})
        tx_1 = Transaction(keyfs, write=True)
        tx_2 = Transaction(keyfs)
        tx_1.delete(D)
        tx_1.commit()
        assert tx_2.get(D) == {1:2}

    def test_not_exist_yields_readonly(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.read_transaction():
            x = D.get()
        assert x == {}
        with pytest.raises(TypeError):
            x[1] = 3

    def test_tx_delete(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.write_transaction():
            D.set({1:1})
        with keyfs.write_transaction():
            D.delete()
            assert not D.exists()

    def test_import_changes(self, keyfs, storage, tmpdir):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.write_transaction():
            D.set({1:1})
        with keyfs.write_transaction():
            D.delete()
        with keyfs.write_transaction():
            D.set({2:2})
        with keyfs.read_transaction() as tx:
            assert tx.get_value_at(D, 0) == {1: 1}
            with pytest.raises(KeyError):
                assert tx.get_value_at(D, 1)
            assert tx.get_value_at(D, 2) == {2: 2}
        with keyfs._storage.get_connection() as conn:
            serial = conn.last_changelog_serial

        assert serial == 2
        # load entries into new keyfs instance
        new_keyfs = KeyFS(tmpdir.join("newkeyfs"), storage)
        D2 = new_keyfs.add_key("NAME", "hello", dict)
        for serial in range(3):
            with keyfs.read_transaction() as tx:
                changes = tx.conn.get_changes(serial)
            new_keyfs.import_changes(serial, changes)
        with new_keyfs.read_transaction() as tx:
            assert tx.get_value_at(D2, 0) == {1:1}
            with pytest.raises(KeyError):
                assert tx.get_value_at(D2, 1)
            assert tx.get_value_at(D2, 2) == {2:2}

    def test_get_value_at_modify_inplace_is_safe(self, keyfs):
        from copy import deepcopy
        D = keyfs.add_key("NAME", "hello", dict)
        d = {1: set(), 2: dict(), 3: []}
        d_orig = deepcopy(d)
        with keyfs.write_transaction():
            D.set(d)
        with keyfs.read_transaction() as tx:
            assert tx.get_value_at(D, 0) == d_orig
            d2 = tx.get_value_at(D, 0)
            with pytest.raises(AttributeError):
                d2[1].add(4)
            with pytest.raises(TypeError):
                d2[2][3] = 5
            with pytest.raises(AttributeError):
                d2[3].append(6)
            assert tx.get_value_at(D, 0) == d_orig

    def test_is_dirty(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.read_transaction():
            assert not D.is_dirty()
        with keyfs.write_transaction():
            assert not D.is_dirty()
            D.set({1:1})
            assert D.is_dirty()
        with keyfs.write_transaction():
            assert not D.is_dirty()

    def future_maybe_test_bounded_cache(self, keyfs):  # if we ever introduce it
        import random
        D = keyfs.add_key("NAME", "hello", dict)
        size = keyfs._storage.CHANGELOG_CACHE_SIZE
        for i in range(size * 3):
            with keyfs.write_transaction():
                D.set({i:i})
            with keyfs.read_transaction():
                D.get()
            assert len(keyfs._storage._changelog_cache) <= \
                   keyfs._storage.CHANGELOG_CACHE_SIZE + 1

        with keyfs.read_transaction() as tx:
            for i in range(size * 2):
                j = random.randrange(0, size * 3)  # noqa: S311
                tx.get_value_at(D, j)
                assert len(keyfs._storage._changelog_cache) <= \
                       keyfs._storage.CHANGELOG_CACHE_SIZE + 1

    def test_import_changes_subscriber(self, keyfs, storage, tmpdir):
        pkey = keyfs.add_key("NAME", "hello/{name}", dict)
        D = pkey(name="world")
        with keyfs.write_transaction():
            D.set({1:1})
        assert keyfs.get_current_serial() == 0
        # load entries into new keyfs instance
        new_keyfs = KeyFS(tmpdir.join("newkeyfs"), storage)
        pkey = new_keyfs.add_key("NAME", "hello/{name}", dict)
        l = []
        new_keyfs.subscribe_on_import(lambda *args: l.append(args))
        with keyfs.read_transaction() as tx:
            changes = tx.conn.get_changes(0)
        new_keyfs.import_changes(0, changes)
        ((serial, changes),) = l
        assert serial == 0
        assert changes == {new_keyfs.NAME(name="world"): ({1: 1}, -1)}

    def test_import_changes_subscriber_error(self, keyfs, storage, tmpdir):
        pkey = keyfs.add_key("NAME", "hello/{name}", dict)
        D = pkey(name="world")
        with keyfs.write_transaction():
            D.set({1: 1})
        keyfs_serial = keyfs.get_current_serial()
        new_keyfs = KeyFS(tmpdir.join("newkeyfs"), storage)
        pkey = new_keyfs.add_key("NAME", "hello/{name}", dict)
        new_keyfs.subscribe_on_import(lambda *args: 0 / 0)
        serial = new_keyfs.get_current_serial()
        with keyfs.read_transaction() as tx:
            changes = tx.conn.get_changes(0)
        with pytest.raises(ZeroDivisionError):
            new_keyfs.import_changes(0, changes)
        # the error in the subscriber didn't prevent the serial from
        # being imported
        assert new_keyfs.get_current_serial() > serial
        assert new_keyfs.get_current_serial() == keyfs_serial

    def test_get_raw_changelog_entry_not_exist(self, keyfs):
        with keyfs.read_transaction() as tx:
            assert tx.conn.get_raw_changelog_entry(10000) is None

    def test_cache_interference(self, storage, tmpdir):
        # because the transaction for the import subscriber was opened during
        # the transaction of the import itself and keys were fetched and placed
        # in the relpath cache, there was a value mismatch when a key from
        # the currently being written serial was places in the cache during
        # the import subscriber run
        keyfs1 = KeyFS(tmpdir.join("keyfs1"), storage)
        pkey1 = keyfs1.add_key("NAME1", "hello1/{name}", dict)
        pkey2 = keyfs1.add_key("NAME2", "hello2/{name}", dict)
        D1 = pkey1(name="world1")
        D2 = pkey2(name="world2")
        for i in range(2):
            with keyfs1.write_transaction():
                assert D1.get() == {}
                D1.set({1: 1})
                assert D1.get() == {1: 1}
            with keyfs1.write_transaction():
                assert D2.get() == {}
                D2.set({1: 1})
                assert D2.get() == {1: 1}
            with keyfs1.write_transaction():
                assert D1.get() == {1: 1}
                D1.set({1: 1, 2: 2})
                assert D1.get() == {1: 1, 2: 2}
            with keyfs1.write_transaction():
                assert D2.get() == {1: 1}
                D2.set({1: 1, 2: 2})
                assert D2.get() == {1: 1, 2: 2}
            with keyfs1.write_transaction():
                assert D1.get() == {1: 1, 2: 2}
                D1.delete()
                assert D1.get() == {}
            with keyfs1.write_transaction():
                assert D2.get() == {1: 1, 2: 2}
                D2.delete()
                assert D2.get() == {}
        # get all changes
        serial1 = keyfs1.get_current_serial()
        with keyfs1.get_connection() as conn1:
            changes1 = [conn1.get_changes(i) for i in range(serial1 + 1)]
        # create new keyfs
        keyfs2 = KeyFS(tmpdir.join("newkeyfs"), storage)
        pkey1 = keyfs2.add_key("NAME1", "hello1/{name}", dict)
        pkey2 = keyfs2.add_key("NAME2", "hello2/{name}", dict)
        D1 = pkey1(name="world1")
        D2 = pkey2(name="world2")

        # add a subscriber to get into that branch in keyfs2.import_changes

        def subscriber(serial, changes):
            # fetch the keys
            if serial % 6 == 0:
                assert D1.get() == {1: 1}, serial
                assert D2.get() == {}, serial
            elif serial % 6 == 1:
                assert D1.get() == {1: 1}, serial
                assert D2.get() == {1: 1}, serial
            elif serial % 6 == 2:
                assert D1.get() == {1: 1, 2: 2}, serial
                assert D2.get() == {1: 1}, serial
            elif serial % 6 == 3:
                assert D1.get() == {1: 1, 2: 2}, serial
                assert D2.get() == {1: 1, 2: 2}, serial
            elif serial % 6 == 4:
                assert D1.get() == {}, serial
                assert D2.get() == {1: 1, 2: 2}, serial
            elif serial % 6 == 5:
                assert D1.get() == {}, serial
                assert D2.get() == {}, serial
            else:
                raise RuntimeError
        keyfs2.subscribe_on_import(subscriber)

        # and import
        for i in range(serial1 + 1):
            keyfs2.import_changes(i, changes1[i])
        changes2 = []
        for i in range(serial1 + 1):
            with keyfs2.get_connection() as conn2:
                changes2.append(conn2.get_changes(i))
        assert serial1 == keyfs2.get_current_serial()
        assert changes1 == changes2


@notransaction
def test_changelog(keyfs):
    D = keyfs.add_key("NAME", "hello", dict)
    with keyfs.write_transaction():
        D.set({1: 1})
    with keyfs.write_transaction():
        D.set({2: 2})
    with keyfs.read_transaction() as tx:
        changes = list(
            tx.iter_serial_and_value_backwards(D.relpath, tx.at_serial))
    assert changes == [
        (1, {2: 2}),
        (0, {1: 1})]


@notransaction
class TestDeriveKey:
    def test_direct_from_file(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.write_transaction():
            D.set({1:1})
        with keyfs.read_transaction() as tx:
            key = tx.derive_key(D.relpath)
        assert key == D
        assert key.params == {}

    def test_pattern_from_file(self, keyfs):
        pkey = keyfs.add_key("NAME", "{name}/{index}", dict)
        params = dict(name="hello", index="world")
        D = pkey(**params)
        with keyfs.write_transaction():
            D.set({1:1})
        assert not hasattr(keyfs, "tx")
        with keyfs.read_transaction() as tx:
            key = tx.derive_key(D.relpath)
        assert key == D
        assert key.params == params

    def test_direct_not_committed(self, keyfs):
        D = keyfs.add_key("NAME", "hello", dict)
        with keyfs.write_transaction():
            D.set({})
            key = keyfs.tx.derive_key(D.relpath)
            assert key == D
            assert key.params == {}

    def test_pattern_not_committed(self, keyfs):
        pkey = keyfs.add_key("NAME", "{name}/{index}", dict)
        params = dict(name="hello", index="world")
        D = pkey(**params)
        with keyfs.write_transaction() as tx:
            D.set({})
            key = tx.derive_key(D.relpath)
            assert key == D
            assert key.params == params


@pytest.fixture
def queue(TimeoutQueue):
    return TimeoutQueue()


@notransaction
class TestSubscriber:
    def test_change_subscription(self, keyfs, queue, pool):
        key1 = keyfs.add_key("NAME1", "hello", int)
        keyfs.notifier.on_key_change(key1, queue.put)
        pool.start()
        with keyfs.write_transaction():
            key1.set(1)
            assert queue.empty()
        event = queue.get()
        assert event.value == 1
        assert event.typedkey == key1
        assert event.at_serial == 0
        assert event.back_serial == -1

    def test_change_subscription_fails(self, keyfs, queue, pool):
        key1 = keyfs.add_key("NAME1", "hello", int)

        def failing(event):
            queue.put("willfail")
            0/0  # noqa: B018

        keyfs.notifier.on_key_change(key1, failing)
        keyfs.notifier.on_key_change(key1, queue.put)
        pool.start()
        with keyfs.write_transaction():
            key1.set(1)
            assert queue.empty()
        msg = queue.get()
        assert msg == "willfail"
        event = queue.get()
        assert event.typedkey == key1

    @pytest.mark.xfail(reason="test monkeypatching wrong thing after refactoring")
    def test_persistent(self, tmpdir, queue, monkeypatch, storage):
        @contextlib.contextmanager
        def make_keyfs():
            keyfs = KeyFS(tmpdir, storage)
            key1 = keyfs.add_key("NAME1", "hello", int)
            keyfs.notifier.on_key_change(key1, queue.put)
            pool = ThreadPool()
            pool.register(keyfs.notifier)
            pool.start()
            try:
                yield key1
            finally:
                pool.kill()

        with make_keyfs() as key1:
            monkeypatch.setattr(key1.keyfs._storage, "_notify_on_commit",
                                lambda x: 0/0)
            # we prevent the hooks from getting called
            with pytest.raises(ZeroDivisionError):
                with key1.keyfs.write_transaction():
                    key1.set(1)
            assert key1.keyfs.get_next_serial() == 1
            assert key1.keyfs.notifier.read_event_serial() == -1
            monkeypatch.undo()

        # and then we restart keyfs and see if the hook still gets called
        with make_keyfs() as key1:
            event = queue.get()
        assert event.at_serial == 0
        assert event.typedkey == key1
        key1.keyfs.notifier.wait_event_serial(0)
        assert key1.keyfs.notifier.read_event_serial() == 0

    def test_subscribe_pattern_key(self, keyfs, queue, pool):
        pkey = keyfs.add_key("NAME1", "{name}", int)
        keyfs.notifier.on_key_change(pkey, queue.put)
        key = pkey(name="hello")
        pool.start()
        with keyfs.write_transaction():
            key.set(1)
        ev = queue.get()
        assert ev.typedkey == key
        assert ev.typedkey.params == {"name": "hello"}
        assert ev.typedkey.name == pkey.name

    @pytest.mark.parametrize("meth", ["wait_event_serial", "wait_tx_serial"])
    def test_wait_event_serial(self, keyfs, pool, queue, meth):
        pkey = keyfs.add_key("NAME1", "{name}", int)
        key = pkey(name="hello")

        class T:
            def __init__(self, num):
                self.num = num

            def thread_run(self):
                if meth == "wait_event_serial":
                    keyfs.notifier.wait_event_serial(self.num)
                else:
                    keyfs.wait_tx_serial(self.num)
                queue.put(self.num)

        for i in range(10):
            pool.register(T(i))
        pool.start()
        for i in range(10):
            with keyfs.write_transaction():
                key.set(i)

        l = [queue.get() for i in range(10)]
        assert sorted(l) == list(range(10))

    def test_wait_tx_same(self, keyfs):
        keyfs.wait_tx_serial(keyfs.get_current_serial())
        keyfs.wait_tx_serial(keyfs.get_current_serial())

    def test_wait_tx_async(self, keyfs, pool, queue):
        from devpi_server.interfaces import IWriter2
        from devpi_server.keyfs_types import Record

        # start a thread which waits for the next serial
        key = keyfs.add_key("NAME", "hello", int)
        wait_serial = keyfs.get_next_serial()

        class T:
            def thread_run(self):
                ret = keyfs.wait_tx_serial(wait_serial, recheck=0.01)
                queue.put(ret)

        pool.register(T())
        pool.start()

        # directly modify the database without keyfs-transaction machinery
        with keyfs._storage.get_connection(write=True) as conn:
            with conn.write_transaction() as _wtx:
                wtx = IWriter2(_wtx)
                wtx.records_set([Record(key, 1, -1, None)])

        # check wait_tx_serial() call from the thread returned True
        assert queue.get() is True

    def test_wait_tx_async_timeout(self, keyfs):
        wait_serial = keyfs.get_next_serial()
        assert not keyfs.wait_tx_serial(wait_serial, timeout=0.001, recheck=0.0001)

    def test_commit_serial(self, keyfs):
        with keyfs.read_transaction() as tx:
            pass
        assert tx.commit_serial is None

        with keyfs.write_transaction() as tx:
            assert tx.commit_serial is None
        assert tx.commit_serial is None

        key = keyfs.add_key("hello", "hello", dict)
        with keyfs.write_transaction() as tx:
            assert tx.at_serial == -1
            tx.set(key, {})
        assert tx.commit_serial == 0

    def test_commit_serial_restart(self, keyfs):
        key = keyfs.add_key("hello", "hello", dict)
        with keyfs.read_transaction() as tx:
            keyfs.restart_as_write_transaction()
            tx.set(key, {})
        assert tx.commit_serial == 0
        assert tx.write

    def test_at_serial_restart(self, keyfs):
        key = keyfs.add_key("hello", "hello", dict)
        with keyfs.read_transaction() as txr:
            tx = Transaction(keyfs, write=True)
            tx.set(key, {1:1})
            tx.commit()
            keyfs.restart_read_transaction()
        assert txr.commit_serial is None
        assert txr.at_serial == 0

    def test_at_serial(self, keyfs):
        with keyfs.read_transaction(at_serial=-1) as tx:
            assert tx.at_serial == -1


@notransaction
def test_crash_recovery(keyfs, storage_info):
    from devpi_server.fileutil import loads
    from pathlib import Path
    if "storage_with_filesystem" not in storage_info.get("_test_markers", []):
        pytest.skip("The storage doesn't have marker 'storage_with_filesystem'.")
    content = b'foo'
    with keyfs.write_transaction() as tx:
        tx.conn.io_file_set('foo', content)
    with keyfs.read_transaction() as tx:
        path = Path(tx.conn.io_file_os_path('foo'))
        raw_changelog_entry = tx.conn.get_raw_changelog_entry(tx.at_serial)
        changelog_entry = loads(raw_changelog_entry)
        tmpname = changelog_entry[1][0]
    tmppath = path.with_name(tmpname)
    assert path.exists()
    assert not tmppath.exists()
    # undo the rename done at transaction end
    path.rename(tmppath)
    assert not path.exists()
    assert tmppath.exists()
    keyfs.finalize_init()
    # the rename should have been performed again
    assert path.exists()
    assert not tmppath.exists()
    with keyfs.write_transaction() as tx:
        tx.conn.io_file_delete('foo')
    assert not path.exists()
    assert not tmppath.exists()
    # put file back in place
    with path.open('wb') as f:
        f.write(content)
    assert path.exists()
    assert not tmppath.exists()
    keyfs.finalize_init()
    # the deletion should have been performed again
    assert not path.exists()
    assert not tmppath.exists()
    with keyfs.write_transaction() as tx:
        tx.conn.io_file_set('foo', content)
    path.unlink()
    # due to the remove file we get an unrecoverable error
    with pytest.raises(OSError, match="missing file"):
        keyfs.finalize_init()


def test_keyfs_sqlite(gen_path, sorted_serverdir):
    from devpi_server import keyfs_sqlite
    tmp = gen_path()
    keyfs = KeyFS(tmp, keyfs_sqlite.Storage)
    with keyfs.write_transaction() as tx:
        assert tx.conn.io_file_os_path('foo') is None
        tx.conn.io_file_set('foo', b'bar')
        tx.conn._sqlconn.commit()
    with keyfs.read_transaction() as tx:
        assert tx.conn.io_file_os_path('foo') is None
        assert tx.conn.io_file_get('foo') == b'bar'
    assert sorted_serverdir(tmp) == [".sqlite_db"]


def test_keyfs_sqlite_fs(gen_path, sorted_serverdir):
    from devpi_server import keyfs_sqlite_fs
    tmp = gen_path()
    keyfs = KeyFS(tmp, keyfs_sqlite_fs.Storage)
    with keyfs.write_transaction() as tx:
        assert tx.conn.io_file_os_path('foo') == str(tmp / 'foo')
        tx.conn.io_file_set('foo', b'bar')
        tx.conn._sqlconn.commit()
    with keyfs.read_transaction() as tx:
        assert tx.conn.io_file_get('foo') == b'bar'
        with open(tx.conn.io_file_os_path('foo'), 'rb') as f:
            assert f.read() == b'bar'
    assert sorted_serverdir(tmp) == [".sqlite", "foo"]


@notransaction
def test_iter_relpaths_at(keyfs):
    pkey = keyfs.add_key("NAME1", "{name}", int)
    key = pkey(name="hello")
    with keyfs.read_transaction() as tx:
        assert list(tx.iter_relpaths_at([key], tx.at_serial)) == []
    with keyfs.write_transaction():
        key.set(1)
    with keyfs.read_transaction() as tx:
        (relpath_info,) = list(tx.iter_relpaths_at([key], tx.at_serial))
    assert relpath_info.keyname == "NAME1"
    assert relpath_info.value == 1
