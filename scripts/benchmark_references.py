"""Offline synthetic context-accounting probe; no models or token/cost claims."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review_ledger.context import Context
from review_ledger.models import Actor, canonical
from review_ledger.service import Ledger
from review_ledger.storage import Store


def size(value):
    encoded = canonical(value)
    return {"chars": len(encoded), "utf8_bytes": len(encoded.encode())}


def run():
    results = []
    for count, body_size in ((1, 100), (3, 1000), (10, 6000)):
        with TemporaryDirectory(prefix="ledger-reference-benchmark-") as directory:
            repo = "synthetic/reference-benchmark"
            ledger = Ledger(Store(Path(directory), "synthetic"), [repo], skill_version="synthetic", skill_hash="a"*64)
            actor = Actor("synthetic-session")
            run = ledger.open({"repository_id":1,"repository_full_name":repo,"number":1,"title":"Synthetic",
                "url":f"https://github.com/{repo}/pull/1","head_sha":"a"*40,"base_sha":"b"*40,
                "comparison":"github_pr","files":[],"files_complete":True,"patches_complete":True,
                "total_files":0,"omitted_files":0,"truncation_reasons":[]},actor,"open")["run"]
            finding = ledger.record(repo,run["id"],actor,1,"finding",{"claim":"Synthetic claim; no defect proven"},"finding")["finding_id"]
            context = Context(ledger,64000,compression_enabled=True)
            common = (repo,run["id"],actor)
            before = context.prepare(*common,query="")
            ids=[]
            for index in range(count):
                external_id = str(index+1)
                source = {"finding_id":finding,"provider":"github","event_type":"issue_comment",
                    "external_id":external_id,"url":f"https://github.com/{repo}/pull/1#issuecomment-{external_id}",
                    "body":("Literal copied text. Unknown truth. "+str(index)+" ")*300,"origin_at":None}
                source["body"] = source["body"][:body_size]
                ids.append(ledger.record(repo,run["id"],actor,1,"external_reference",source,"ref-"+external_id)["reference_id"])
            for mode in ("full","compact"):
                prepare_request = {"action":"prepare","query":"","mode":mode,"max_chars":64000}
                prepared = context.prepare(*common,query="",mode=mode,max_chars=64000)
                assert prepared["state"] == "ok"
                details=[]
                detail_requests=[]
                for ident in ids:
                    detail_requests.append({"action":"detail","kind":"external_reference","record_id":ident,"mode":mode,"max_chars":64000})
                    detail = context.detail(*common,kind="external_reference",record_id=ident,mode=mode,max_chars=64000)
                    assert detail["state"] == "detail" and detail["complete"] is True
                    details.append(detail)
                resume_request={"action":"resume","manifest_id":prepared["manifest_id"]}
                resumed=context.resume(*common,manifest_id=prepared["manifest_id"])
                requests=[prepare_request,*detail_requests,resume_request]
                responses=[prepared,*details,resumed]
                results.append({"references":count,"body_chars_each":body_size,"mode":mode,
                    "cap_chars":64000,"before_any_reference_prepare":size(before),
                    "metadata_only_prepare":size(prepared),"all_detail_responses":size(details),
                    "resume":size(resumed),"request_count":len(requests),
                    "trajectory_chars":sum(size(x)["chars"] for x in requests+responses),
                    "trajectory_utf8_bytes":sum(size(x)["utf8_bytes"] for x in requests+responses),
                    "metadata_prepare_delta_chars":size(prepared)["chars"]-size(before)["chars"],
                    "body_preserved":all(len(x["content"]["body"])==body_size for x in details)})
    return {"kind":"synthetic_reference_trajectory_accounting","cases":results,
        "scope":"Serialized operation payloads and returned JSON; retrieval requests omit common scope/host envelope equally in every case. All selected bodies deliberately retrieved, not a model-driven policy.",
        "limitations":["Added metadata is additional cost, not a measured efficiency benefit.",
            "No provider/model token counter, billing, live review quality or model comprehension measurement.",
            "Legacy-before-reference row is a cost baseline, not an equivalent-information competitor.",
            "No network, inference, corpus collection or personal profile used."],
        "provider_tokens":None,"review_quality":None,"net_token_savings":None}

if __name__ == "__main__":
    print(json.dumps(run(),indent=2,ensure_ascii=False))
