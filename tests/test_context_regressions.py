"""Synthetic defensive regressions for context provenance and resume completeness."""
import json
import pytest
from review_ledger.models import LedgerError
from review_ledger.context import Context
from review_ledger.learning import Learning
from review_ledger.models import canonical
from review_ledger.skills import Skills
from review_ledger.reports import export

REPO='synthetic/example'
def test_more_than_five_lessons_include_next_page_reference(ledger,opened,actor,lesson_data):
    learn=Learning(ledger.store); scope=ledger.scope(REPO)
    for i in range(6):
        data={**lesson_data,'question':f'retry question {i}'}
        p=learn.propose(scope,opened['id'],actor,1,data,f'propose{i}')
        learn.operator(scope,p['version_id'],'approve','synthetic',f'approve{i}')
    out=Context(ledger,64000).prepare(REPO,opened['id'],actor,query='retry')
    assert len([x for x in out['records'] if x['kind']=='lesson'])==5
    assert any(item.get('reason') == 'result limit' and item.get('next_result_offset') == 5 for item in out['omitted'])

def test_unassessed_findings_survive_resume(ledger,opened,actor):
    f=ledger.record(REPO,opened['id'],actor,1,'finding',{'claim':'Pending question: retry can double charge'},'finding')
    out=Context(ledger,64000).resume(REPO,opened['id'],actor)
    assert f['finding_id'] in canonical(out)
    assert 'Pending question' in canonical(out)
    assert out['omitted']==[]

def test_revoked_selection_export_retains_identity_and_marks_ineligible(ledger,opened,actor,tmp_path):
    p=tmp_path/'sk';p.mkdir();(p/'SKILL.md').write_text('---\nname: example\ndescription: Synthetic\n---\nFull instructions.\n')
    scope=ledger.scope(REPO); skills=Skills(ledger.store)
    skill=skills.register(scope,p,qualified_id='synthetic/example',approved=True,enabled=True)
    out=Context(ledger,64000,skills_enabled=True).prepare(REPO,opened['id'],actor,query='retry')
    skills.disable(scope,skill['id'],reason='Synthetic revocation',request_key='revoke')
    report=export(ledger,REPO,opened['id'],actor,format='json')['content']
    assert skill['id'] in report
    data=json.loads(report)
    selected=next(s for m in data['context_manifests'] for s in m['selections'] if s['id']==skill['id'])
    assert selected['eligible_now'] is False
    assert 'Full instructions.' not in report
    assert out['manifest_id'] in report

def test_repeated_resume_preserves_structured_selectors(ledger,opened,actor,tmp_path):
    p=tmp_path/'sk';p.mkdir();(p/'SKILL.md').write_text('---\nname: example\ndescription: Synthetic\napplicability:\n  tags: [retry]\n---\nFull instructions.\n')
    Skills(ledger.store).register(ledger.scope(REPO),p,qualified_id='synthetic/example',approved=True,enabled=True)
    context=Context(ledger,64000,skills_enabled=True)
    first=context.prepare(REPO,opened['id'],actor,query='retry',tags=['retry'])
    second=context.resume(REPO,opened['id'],actor,manifest_id=first['manifest_id'])
    third=context.resume(REPO,opened['id'],actor,manifest_id=second['manifest_id'])
    get=lambda out:next(r for r in out['records'] if r['kind']=='skill')['applicability']
    assert get(second)['state']=='matched'
    assert get(third)['state']=='matched'

def test_backup_verifies_missing_immutable_resource(ledger,opened,tmp_path):
    p=tmp_path/'sk';p.mkdir();(p/'SKILL.md').write_text('---\nname: example\ndescription: Synthetic\n---\nFull instructions.\n');(p/'notes.md').write_text('Critical imported text')
    skill=Skills(ledger.store).register(ledger.scope(REPO),p,qualified_id='synthetic/example',references=['notes.md'],approved=True,enabled=True)
    with ledger.store.connect() as conn:
        conn.execute('DELETE FROM optional_skill_resources WHERE version_id=?',(skill['id'],))
    with pytest.raises(LedgerError, match='digest mismatch'):
        ledger.store.backup()
    assert not list((ledger.store.data_dir / 'backups').glob('*.sqlite3'))

def test_duplicate_skill_content_preserves_both_identities_once(ledger,opened,actor,tmp_path):
    p=tmp_path/'sk';p.mkdir();(p/'SKILL.md').write_text('---\nname: example\ndescription: Synthetic\n---\nIdentical required procedure unit.\n')
    skills=Skills(ledger.store);scope=ledger.scope(REPO)
    a=skills.register(scope,p,qualified_id='first/example',approved=True,enabled=True)
    b=skills.register(scope,p,qualified_id='second/example',approved=True,enabled=True)
    assert a['content_digest']==b['content_digest']
    out=Context(ledger,64000,skills_enabled=True).prepare(REPO,opened['id'],actor,query='retry')
    records=[r for r in out['records'] if r['kind']=='skill']
    assert len(records)==1
    assert {r['id'] for r in records[0]['sources']} == {a['id'], b['id']}
    assert {r['qualified_id'] for r in records[0]['sources']} == {'first/example', 'second/example'}

def test_detail_rejects_known_repository_mismatch(ledger,opened,actor,tmp_path):
    p=tmp_path/'sk';p.mkdir();(p/'SKILL.md').write_text('---\nname: example\ndescription: Synthetic\napplicability:\n  repositories: [synthetic/other]\n---\nInstructions for another architecture.\n')
    skill=Skills(ledger.store).register(ledger.scope(REPO),p,qualified_id='synthetic/example',approved=True,enabled=True)
    ctx=Context(ledger,64000,skills_enabled=True)
    prepared=ctx.prepare(REPO,opened['id'],actor,query='retry')
    assert not any(r['kind']=='skill' for r in prepared['records'])
    with pytest.raises(LedgerError) as error:
        ctx.detail(REPO,opened['id'],actor,kind='skill',record_id=skill['id'])
    assert error.value.code == 'context_inapplicable'
