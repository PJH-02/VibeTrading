from test_checkpoint_startup import producer,capture,append,event,engine,replay_checkpoint_startup,pytest

@pytest.mark.asyncio
async def test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority_oldest(tmp_path,monkeypatch):
    item=producer(tmp_path)
    source,codec,payload=capture([item])
    append(item,[event('a',2)])
    target=engine([item])
    restore=target.restore_virgin

    def restore_then_replace(*args,**kwargs):
        result=restore(*args,**kwargs)
        segment=min((path for path in item.spool_root.iterdir() if path.suffix in {'.active','.sealed'}), key=lambda path:path.name)
        replacement=segment.with_suffix('.replacement')
        replacement.write_bytes(segment.read_bytes())
        replacement.replace(segment)
        return result

    monkeypatch.setattr(target,'restore_virgin',restore_then_replace)
    try:
        with pytest.raises(RuntimeError,match='identity changed'):
            await replay_checkpoint_startup(target,[item],codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert target.stats['closed'] is True
        assert target.stats['replayed']==0
    finally:
        await target.close();await source.close()

@pytest.mark.asyncio
async def test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority_newest(tmp_path,monkeypatch):
    item=producer(tmp_path)
    source,codec,payload=capture([item])
    append(item,[event('a',2)])
    target=engine([item])
    restore=target.restore_virgin

    def restore_then_replace(*args,**kwargs):
        result=restore(*args,**kwargs)
        segment=max((path for path in item.spool_root.iterdir() if path.suffix in {'.active','.sealed'}), key=lambda path:path.name)
        replacement=segment.with_suffix('.replacement')
        replacement.write_bytes(segment.read_bytes())
        replacement.replace(segment)
        return result

    monkeypatch.setattr(target,'restore_virgin',restore_then_replace)
    try:
        with pytest.raises(RuntimeError,match='identity changed'):
            await replay_checkpoint_startup(target,[item],codec=codec,payload=payload,
                expected_engine_incarnation=source.hot_path.engine_incarnation)
        assert target.stats['closed'] is True
        assert target.stats['replayed']==0
    finally:
        await target.close();await source.close()

