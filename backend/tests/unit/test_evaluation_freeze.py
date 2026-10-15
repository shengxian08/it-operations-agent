import json
import subprocess
import sys
from pathlib import Path


def test_it_evaluation_freeze_and_four_strategy_jobs(tmp_path):
    root=Path(__file__).resolve().parents[3]
    result=subprocess.run([sys.executable,str(root/'scripts/agent_evaluation.py'),
        '--export-jobs',str(tmp_path/'jobs.jsonl')],cwd=root,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    jobs=[json.loads(line) for line in (tmp_path/'jobs.jsonl').read_text().splitlines()]
    assert len(jobs)==120*3*4
    assert {j['repeat'] for j in jobs}=={1,2,3}
    assert len({j['case']['prompt'] for j in jobs})==120
    assert {j['strategy']['id'] for j in jobs}=={'A','B','C','D'}
