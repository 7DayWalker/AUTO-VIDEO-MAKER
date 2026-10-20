"""Track 7 Mode C — Series / Episodic creation seam (additive over Mode B)."""
import pytest
from app.orchestrator.production_execution import ProductionExecutor, UnitExecutionState
from app.production.storage import load_production
from app.web import server as web
def _base(**o):
    r={"mode":"single_video","creation_method":"local","title":"T7 video","brief":"A concise local brief.","output_intent":"youtube_shorts","target_duration":30,"scene_count":6,"tone":"clear"}
    r.update(o);return r
def test_single_video_still_creates_one_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base())
    assert s==201
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert prod.command.workload_shape=="single_video"
    assert len(prod.units)==1
def test_unit_count_two_creates_exactly_two_units(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=2))
    assert s==201;assert p["unit_count"]==2
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert prod.command.workload_shape=="unit_count"
    assert prod.command.intended_unit_count==2
    assert [u.unit_id for u in prod.units]==["unit-001","unit-002"]
    assert [u.ordinal for u in prod.units]==[1,2]
    for u in prod.units:
        assert u.unit_type=="standalone_video";assert u.dependencies==[];assert u.episode is None
def test_unit_count_20_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=20))
    assert s==201
def test_unit_count_1_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload(_base(mode="unit_count",unit_count=1))
    assert s==400
def test_unit_count_21_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload(_base(mode="unit_count",unit_count=21))
    assert s==400
def test_missing_unit_count_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    b=_base(mode="unit_count");b.pop("unit_count",None)
    s,_=web.create_production_payload(b)
    assert s==400
def test_invalid_unit_count_type_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    for bad in ["2", 2.0, True, {}, []]:
        s,_=web.create_production_payload(_base(mode="unit_count",unit_count=bad))
        assert s==400
def test_default_titles_derived(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=3, title="My Batch"))
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert [u.title for u in prod.units]==["My Batch \u2014 Part 1","My Batch \u2014 Part 2","My Batch \u2014 Part 3"]
def test_supplied_titles_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=2, unit_titles=["Alpha","Beta"]))
    assert s==201
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert [u.title for u in prod.units]==["Alpha","Beta"]
def test_supplied_titles_mismatch_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload(_base(mode="unit_count",unit_count=2, unit_titles=["Only one"]))
    assert s==400
def test_command_shape_and_count_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=4))
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert prod.command.workload_shape=="unit_count"
    assert prod.command.intended_unit_count==4
def test_every_unit_invariants(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=5))
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert len({u.unit_id for u in prod.units})==5
    assert [u.ordinal for u in prod.units]==[1,2,3,4,5]
    for u in prod.units:
        assert u.unit_type=="standalone_video";assert u.dependencies==[];assert u.episode is None
def test_persists_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=2, unit_titles=["A","B"]))
    import json
    raw=json.loads((tmp_path/"productions"/p["production_id"]/"production.json").read_text(encoding="utf-8"))
    assert raw["command"]["workload_shape"]=="unit_count";assert len(raw["units"])==2
def test_derive_progress_multi(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    monkeypatch.setattr(web,"PROJECTS_DIR",tmp_path/"projects")
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=3))
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    ex=ProductionExecutor(prod.production_id,productions_root=tmp_path/"productions",projects_root=tmp_path/"projects")
    rep=ex.derive_progress();assert len(rep.units)==3
    assert all(u.state==UnitExecutionState.NOT_STARTED for u in rep.units)
def test_failed_does_not_block_independent(tmp_path):
    from app.production.models import Production,ProductionCommand,ProductionUnit
    from app.orchestrator.production_execution import UnitProgress
    cmd=ProductionCommand(text="batch",workload_shape="unit_count",intended_unit_count=2,explicit_constraints={"mode":"unit_count"},creation_context={"source":"test"},issued_at="2026-09-30T00:00:00")
    units=[ProductionUnit(unit_id="unit-001",ordinal=1,unit_type="standalone_video",title="A",premise="A",content_intent={},dependencies=[]),ProductionUnit(unit_id="unit-002",ordinal=2,unit_type="standalone_video",title="B",premise="B",content_intent={},dependencies=[])]
    prod=Production(production_id="prd_20260930_batch_001",title="Batch",command=cmd,units=units,created_at="2026-09-30T00:00:00")
    states={"unit-001":UnitProgress(unit_id="unit-001",ordinal=1,state=UnitExecutionState.FAILED),"unit-002":UnitProgress(unit_id="unit-002",ordinal=2,state=UnitExecutionState.INCOMPLETE)}
    ex=ProductionExecutor(prod.production_id,productions_root=tmp_path/"productions")
    rep=ex._finalize_progress(prod,dict(states))
    by={u.unit_id:u.state for u in rep.units}
    assert by["unit-001"]==UnitExecutionState.FAILED;assert by["unit-002"]==UnitExecutionState.INCOMPLETE
def test_route_independence(tmp_path, monkeypatch):
    for strat in ["local","alibaba_image_local_video","alibaba_video"]:
        monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/f"r_{strat}")
        s,p=web.create_production_payload(_base(mode="unit_count",unit_count=2,generation_strategy=strat))
        assert s==201;assert p["generation_strategy"]==strat
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"r_explicit")
    s,p=web.create_production_payload({**_base(mode="unit_count",unit_count=2),"route":"full_ai_video"})
    assert s==201;assert p["route"]=="full_ai_video"
def test_unknown_mode_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload(_base(mode="series"))
    assert s==400
def test_no_new_engine(tmp_path):
    import app.web.server as s
    assert not hasattr(s,"BatchExecutor");assert not hasattr(s,"SeriesExecutor")
def test_canonical_model_still_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    from app.content.model_registry import list_models
    ms=list_models()
    if not ms:
        pytest.skip("no registered models")
    mid=ms[0].id
    s,p=web.create_production_payload(_base(mode="unit_count",unit_count=2,canonical_model_id=mid))
    assert s==201;assert p["canonical_model_id"]==mid
def test_episodes_implies_episodic(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"p2")
    s,p=web.create_production_payload({"mode":"unit_count","creation_method":"local","title":"Series S","brief":"series brief","output_intent":"youtube_shorts","unit_count":2,"episodes":[{"title":"E1"},{"title":"E2"}]})
    assert s==201
    prod=load_production(p["production_id"],root=tmp_path/"p2")
    assert all(u.unit_type=="episode" for u in prod.units)
def test_unit_titles_episodes_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"p2")
    r={"mode":"unit_count","creation_method":"local","title":"Series S","brief":"series brief","output_intent":"youtube_shorts","unit_count":3,"unit_type":"episode","unit_titles":["A","B","C"],"episodes":[{"title":"E1"},{"title":"E2"},{"title":"E3"}]}
    s,_=web.create_production_payload(r)
    assert s==400
