#!/usr/bin/env python3
"""Plan connector reads, then compile evidence-backed interest changes and an audit.

The agent calls list_projects/list_threads/read_thread. This script never scrapes
ChatGPT, opens credentials, calls a model, or executes instructions in source text.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=result.tzinfo or timezone.utc)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def unique(values):
    return list(dict.fromkeys(value for value in values if value))


def included(record: dict, registry: dict) -> bool:
    if record.get("thread_id", record.get("id")) in registry.get("exclude_thread_ids", []):
        return False
    title = str(record.get("title", "")).casefold()
    if any(term.casefold() in title for term in registry.get("exclude_title_terms", [])):
        return False
    projects = registry.get("include_projects", [])
    project_id = record.get("project_id", record.get("projectId"))
    if project_id in registry.get("exclude_project_ids", []):
        return False
    if project_id in {p["project_id"] for p in projects}:
        return True
    cwd = record.get("cwd")
    if cwd and any(p.get("path") and Path(cwd).resolve().is_relative_to(Path(p["path"]).resolve()) for p in projects):
        return True
    thread_id = record.get("thread_id", record.get("id"))
    if thread_id in {t["thread_id"] for t in registry.get("tracked_threads", [])}:
        return True
    return any(term.casefold() in title for term in registry.get("discovery_terms", []))


def build_read_plan(inventory: dict, registry: dict, state: dict, now: datetime) -> dict:
    settings = registry.get("refresh", {})
    cutoff = now - timedelta(days=int(settings.get("lookback_days", 30)))
    tracked = {t["thread_id"]: dict(t) for t in registry.get("tracked_threads", [])}
    for source_id, item in state.get("sources", {}).items():
        if item.get("thread_id"):
            tracked.setdefault(item["thread_id"], dict(item))
    rows = {str(t["id"]): dict(t) for t in inventory.get("threads", []) if included(t, registry)}
    for thread_id, item in tracked.items():
        if thread_id not in rows:
            rows[thread_id] = {**item, "id":thread_id, "not_in_recent_inventory":True}
    requests, unchanged = [], []
    for thread_id, row in rows.items():
        kind = row.get("kind", "codex")
        source_id = f"{kind}:{thread_id}"
        old = state.get("sources", {}).get(source_id, {})
        updated = row.get("updatedAt", row.get("updated_at"))
        updated_at = (datetime.fromtimestamp(updated, timezone.utc).isoformat()
                      if isinstance(updated, (int, float)) else str(updated or ""))
        if not old and thread_id not in tracked and updated_at and instant(updated_at) < cutoff:
            continue
        same = bool(old and updated_at and old.get("updated_at") and instant(updated_at) == instant(old["updated_at"]))
        last = instant(old["last_read_at"]) if old.get("last_read_at") else None
        recheck = last is None or now-last >= timedelta(days=int(settings.get("tracked_recheck_days", 7)))
        if same and not recheck:
            unchanged.append(source_id)
            continue
        requests.append({"source_id":source_id, "thread_id":thread_id,
                         "kind":kind, "host_id":row.get("hostId", row.get("host_id")),
                         "project_id":row.get("projectId", row.get("project_id")),
                         "cwd":row.get("cwd"), "title":row.get("title", ""),
                         "updated_at":updated_at, "turn_limit":int(settings.get("turn_limit", 6)),
                         "reason":"new_or_changed" if not same else "periodic_recheck",
                         "not_in_recent_inventory":bool(row.get("not_in_recent_inventory"))})
    requests.sort(key=lambda x:(x["updated_at"], x["kind"] == "chatgpt"), reverse=True)
    cap = int(settings.get("max_thread_reads", 16))
    files=[]
    for item in registry.get("project_files", []):
        path=Path(item["path"])
        entry={**item,"source_id":"file:"+str(path),"exists":path.is_file()}
        if path.is_file():
            entry["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
            entry["needs_read"]=entry["sha256"] != state.get("sources",{}).get(entry["source_id"],{}).get("file_sha256")
        else:
            entry["needs_read"]=True
        files.append(entry)
    return {"schema_version":1,"checked_at":now.isoformat(),"thread_reads":requests[:cap],
            "deferred_thread_reads":requests[cap:],"unchanged_sources":unchanged,"project_files":files,
            "coverage":{"inventory_limit":inventory.get("inventory_limit",50),
                        "inventory_returned":len(inventory.get("threads",[])),
                        "all_history_scanned":False,"unavailable_sources":inventory.get("unavailable_sources",[]),
                        "limitation":"Recent connector inventory plus retained tracked IDs; not all historical conversations."}}


def collect_files(bundle: dict, registry: dict) -> dict:
    result=copy.deepcopy(bundle)
    existing={source["source_id"] for source in result.setdefault("sources",[])}
    for entry in registry.get("project_files",[]):
        path=Path(entry["path"]); source_id="file:"+str(path)
        if source_id in existing:
            continue
        source={"source_id":source_id,"kind":"project_file","path":str(path),
                "project_id":entry.get("project_id"),"title":entry.get("label",path.name),
                "retrieved_at":result["checked_at"],"status":"unavailable","content":""}
        try:
            # Only exact configured files, no recursive directory or mailbox scan.
            raw=path.read_bytes()
            maximum=int(registry.get("refresh",{}).get("max_project_file_chars",16000))
            content=raw.decode("utf-8-sig")
            source.update(content=content[:maximum],file_sha256=hashlib.sha256(raw).hexdigest(),
                          updated_at=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
                          status="partial" if len(content)>maximum else "ok",
                          coverage={"truncated":len(content)>maximum})
        except (OSError,UnicodeDecodeError) as exc:
            source["error"]=str(exc)
        result["sources"].append(source)
    return result


def support(evidence: list[dict], sources: dict[str,dict]) -> list[dict]:
    if not evidence:
        raise ValueError("Every interest change needs exact source evidence")
    result=[]
    for item in evidence:
        source=sources.get(item.get("source_id"))
        quote=str(item.get("quote","")).strip()
        if source is None or source.get("status") not in {"ok","partial"}:
            raise ValueError("Evidence source was not successfully read: "+str(item.get("source_id")))
        if source.get("kind") in {"chatgpt","codex"}:
            messages=[m for m in source.get("messages",[]) if m.get("role")=="user"]
            matches=[m for m in messages if quote in m.get("text","") and
                     (not item.get("turn_id") or item["turn_id"]==m.get("turn_id"))]
            if len(quote)<8 or not matches:
                raise ValueError("Chat evidence must quote an actually returned user message")
            timestamp=max(m.get("timestamp",source.get("updated_at","")) for m in matches)
        else:
            if len(quote)<8 or quote not in source.get("content",""):
                raise ValueError("Project evidence must quote actual file content")
            timestamp=source.get("updated_at",source.get("retrieved_at"))
        result.append({"source_id":source["source_id"],"title":source.get("title",""),
                       "quote":quote,"turn_id":item.get("turn_id"),"supported_at":timestamp,
                       "uri":source.get("uri",source.get("path",""))})
    return result


def compile_refresh(previous: dict, old_state: dict, bundle: dict, decisions: dict,
                    registry: dict, report_date: str) -> tuple[dict,dict,dict]:
    now=instant(bundle["checked_at"])
    sources={source["source_id"]:source for source in bundle.get("sources",[])}
    for source in sources.values():
        if source.get("kind") == "project_file":
            if source.get("path") not in {entry["path"] for entry in registry.get("project_files",[])}:
                raise ValueError("Project file outside configured scope")
        elif not included(source,registry):
            raise ValueError("Chat outside configured research scope: "+source["source_id"])
    state=copy.deepcopy(old_state or {})
    state.setdefault("schema_version",1)
    state.setdefault("baseline_profile",copy.deepcopy(previous))
    interests=state.setdefault("interests",{})
    settings=registry.get("refresh",{})
    changes=[]
    for proposal in decisions.get("upsert_interests",[]):
        key=str(proposal["id"])
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",key):
            raise ValueError("Invalid interest identifier")
        proof=support(proposal.get("evidence",[]),sources)
        old=interests.get(key)
        strength=proposal.get("strength",old.get("strength","exploratory") if old else "exploratory")
        if strength not in {"explicit","repeated","exploratory"}:
            raise ValueError("Invalid interest strength")
        keywords=unique((old or {}).get("keywords",[])+[str(value).strip() for value in proposal.get("keywords",[])][:12])
        new_queries={kind:str(value).strip() for kind,value in proposal.get("queries",{}).items()
                     if kind in {"direct","mechanism","method","application","review","frontier_transfer"} and str(value).strip()}
        queries={**(old or {}).get("queries",{}),**new_queries}
        variants=copy.deepcopy((old or {}).get("query_variants",{}))
        for kind,query in (old or {}).get("queries",{}).items():
            variants[kind]=unique(variants.get(kind,[])+[query])
        for kind,query in new_queries.items():
            variants[kind]=unique(variants.get(kind,[])+[query])
        if not keywords or not queries.get("direct") or any(len(q)>512 for q in queries.values()):
            raise ValueError("Interests need bounded keywords and a direct query")
        proof=list({digest(item):item for item in (old or {}).get("evidence",[])+proof}.values())
        days=int(settings.get("exploratory_ttl_days",21) if strength=="exploratory" else settings.get("interest_ttl_days",60))
        latest=max(e["supported_at"] for e in proof)
        # Re-reading the same source does not refresh its scientific support date.
        item={"id":key,"label":proposal["label"],"summary":proposal["summary"],
              "keywords":keywords,"queries":queries,"query_variants":variants,"strength":strength,
              "weight":{"explicit":1.1,"repeated":0.95,"exploratory":0.7}[strength],
              "evidence":proof,"first_seen_at":old.get("first_seen_at",now.isoformat()) if old else now.isoformat(),
              "last_supported_at":latest,"expires_at":(instant(latest)+timedelta(days=days)).isoformat(),
              "status":"active"}
        scientific=lambda x:{k:v for k,v in x.items() if k not in {"first_seen_at"}}
        if old is None or digest(scientific(item))!=digest(scientific(old)):
            changes.append({"action":"added" if old is None else "updated","interest_id":key,"label":item["label"],"reason":proposal["summary"],"evidence":proof})
        interests[key]=item
    for proposal in decisions.get("deactivate_interests",[]):
        if not proposal.get("user_requested",False):
            raise ValueError("Interest deactivation requires an explicit user request")
        proof=support(proposal.get("evidence",[]),sources)
        key=proposal["id"]
        if key in interests and interests[key]["status"]=="active":
            interests[key]["status"]="inactive"
            changes.append({"action":"deactivated","interest_id":key,"label":interests[key]["label"],"reason":proposal["reason"],"evidence":proof})
    # Default is cumulative retention. Age of evidence may guide attention,
    # but cannot erase interests without an explicit user request.
    for key,item in interests.items():
        if not settings.get("allow_automatic_expiry",False) and item["status"]=="expired":
            item["status"]="active"
            changes.append({"action":"restored","interest_id":key,"label":item["label"],"reason":"Cumulative user-requested retention; old keywords are preserved"})
        elif settings.get("allow_automatic_expiry",False) and item["status"]=="active" and instant(item["expires_at"])<now:
            item["status"]="expired"
            changes.append({"action":"expired","interest_id":key,"label":item["label"],"reason":"Explicitly configured automatic expiry"})
    active=sorted((i for i in interests.values() if i["status"]=="active"),key=lambda i:(i["last_supported_at"],i["weight"]),reverse=True)
    profile=copy.deepcopy(state["baseline_profile"])
    profile["schema_version"]=1
    profile["retrieval_mode"]="extend"
    profile["context_applied"]=True
    baseline_summary=profile.get("focus_summary","")
    profile["focus_summary"]=baseline_summary+(" 当前动态关注："+"；".join(i["label"] for i in active) if active else "")
    profile["primary_topics"]={**profile.get("primary_topics",{}),**copy.deepcopy(previous.get("primary_topics",{}))}
    inactive=[item for item in interests.values() if item["status"]!="active"]
    for item in inactive:
        profile["primary_topics"].pop("context_"+item["id"],None)
    for item in active:
        profile["primary_topics"]["context_"+item["id"]]={"label":item["label"],"category":"Dynamic research interests","weight":item["weight"],"keywords":item["keywords"]}
    cap=int(settings.get("max_context_modules",4))
    # Rotate lower-priority active topics without replacing persistent broad modules.
    selected=active
    if len(active)>cap:
        anchored=active[:min(2,cap)]
        rotating=active[len(anchored):]
        offset=datetime.fromisoformat(report_date).toordinal()%len(rotating)
        selected=anchored+(rotating[offset:]+rotating[:offset])[:cap-len(anchored)]
    profile["context_modules"]={item["id"]:{"label":item["label"],"topic_keys":["context_"+item["id"]],"queries":item["queries"],"context_interest":True,
        "preferred_sources":["crossref","europepmc"],"optional_sources":["openalex","semantic_scholar","webofscience"]} for item in selected}
    baseline_queries=profile.get("retrieval_queries",[])
    active_queries=unique([q for item in active for values in item.get("query_variants",{}).values() for q in values]+[q for item in active for q in item["queries"].values()])
    inactive_queries={q for item in inactive for values in item.get("query_variants",{}).values() for q in values}|{q for item in inactive for q in item["queries"].values()}
    removable=inactive_queries-set(active_queries)-set(baseline_queries)
    # The retained library is cumulative; only the daily execution modules rotate.
    profile["retrieval_queries"]=unique([q for q in previous.get("retrieval_queries",[]) if q not in removable]+baseline_queries+
        [q for item in active for q in item.get("query_variants",{}).get("direct",[])]+[item["queries"]["direct"] for item in active])
    profile["europepmc_terms"]=unique(previous.get("europepmc_terms",[])+profile.get("europepmc_terms",[])+[word for item in active for word in item["keywords"][:2]])
    old_keywords=unique(previous.get("keyword_bank",[])+[word for topic in previous.get("primary_topics",{}).values() for word in topic.get("keywords",[])])
    current_keywords=unique([word for topic in profile["primary_topics"].values() for word in topic.get("keywords",[])])
    removed_by_request={word for item in inactive for word in item.get("keywords",[])}-set(current_keywords)
    profile["keyword_bank"]=unique([word for word in old_keywords if word not in removed_by_request]+current_keywords)
    for field in ("secondary_topics","domain_keywords","negative_keywords","target_journals"):
        if field in previous or field in profile:
            profile[field]=unique(previous.get(field,[])+profile.get(field,[]))
    evidence=profile.setdefault("evidence",{})
    evidence["chat_basis"]=unique(evidence.get("chat_basis",[])+["动态关注："+i["summary"] for i in active])
    evidence["project_files"]=unique(evidence.get("project_files",[])+[s["path"] for s in sources.values() if s.get("kind")=="project_file" and s.get("status") in {"ok","partial"}])
    evidence["source_records"]=[e for item in active for e in item["evidence"]]
    discovery_errors=[name for name,status in bundle.get("discovery",{}).items() if status.get("status") not in {"ok","unchanged"}]
    failed=[s["source_id"] for s in sources.values() if s.get("status") in {"failed","unavailable"}]
    partial=[s["source_id"] for s in sources.values() if s.get("status")=="partial"]
    status="degraded" if discovery_errors or failed else "updated" if changes else "no_change"
    refresh={"checked_at":bundle["checked_at"],"report_date":report_date,"status":status,
             "web_chats_read":sum(s.get("kind")=="chatgpt" and s.get("status") in {"ok","partial"} for s in sources.values()),
             "codex_chats_read":sum(s.get("kind")=="codex" and s.get("status") in {"ok","partial"} for s in sources.values()),
             "project_files_read":sum(s.get("kind")=="project_file" and s.get("status") in {"ok","partial"} for s in sources.values()),
             "failed_sources":failed,"partial_sources":partial,"discovery_errors":discovery_errors,
             "active_interests":[i["id"] for i in active],"searched_interests":[i["id"] for i in selected],
             "all_history_scanned":False,"interest_change_count":len(changes),
             "keyword_update_mode":"additive","retained_keyword_count":len(set(old_keywords)&set(profile["keyword_bank"])),
             "automatic_expiry":bool(settings.get("allow_automatic_expiry",False))}
    profile["_context_refresh"]=refresh
    for source in sources.values():
        if source.get("status") in {"ok","partial"}:
            state.setdefault("sources",{})[source["source_id"]]={key:source.get(key) for key in ["thread_id","kind","host_id","project_id","cwd","title","updated_at","file_sha256"]}
            state["sources"][source["source_id"]].update(last_read_at=bundle["checked_at"],content_sha256=digest(source.get("content","")))
    state["last_refresh"]=refresh
    query_change=previous.get("retrieval_queries",[])!=profile.get("retrieval_queries",[]) or previous.get("context_modules",{})!=profile.get("context_modules",{})
    audit={"schema_version":1,"refresh":refresh,"discovery":bundle.get("discovery",{}),"source_checks":[{k:s.get(k) for k in ["source_id","title","kind","status","updated_at","coverage","error"]} for s in sources.values()],
           "interest_changes":changes,"retrieval_queries_changed":query_change,
           "keyword_changes":{"mode":"additive","retained":sorted(set(old_keywords)&set(profile["keyword_bank"])),
                              "added":sorted(set(profile["keyword_bank"])-set(old_keywords)),
                              "removed":sorted(set(old_keywords)-set(profile["keyword_bank"]))},
           "previous_profile_sha256":digest(previous),"new_profile_sha256":digest(profile),
           "before_queries":previous.get("retrieval_queries",[]),"after_queries":profile.get("retrieval_queries",[]),
           "preserved":"Persistent core, CNS journal watch, broad methods, publication mix and report size remain in base config."}
    return profile,state,audit


def render_changes(audit: dict) -> str:
    refresh=audit["refresh"]
    lines=["# 检索兴趣刷新", "", f"日期：{refresh['report_date']}；状态：{refresh['status']}", "",
           f"读取：网页版聊天 {refresh['web_chats_read']}；Codex 聊天 {refresh['codex_chats_read']}；项目文件 {refresh['project_files_read']}。", "",
           "## 今日变化", ""]
    if not audit["interest_changes"]:
        lines.append("未发现有证据支持的兴趣变化；沿用已有兴趣。" if refresh["status"]!="degraded" else "来源刷新不完整；保留已有兴趣，不能将本次失败解释为没有新兴趣。")
    for change in audit["interest_changes"]:
        lines.append(f"- **{change['label']}**（{change['action']}）：{change['reason']}")
        for evidence in change.get("evidence",[])[:2]:
            uri=evidence.get("uri","")
            lines.append(f"  - 来源：[{evidence['title']}]({uri})；依据：{evidence['quote'][:200]}")
    lines.extend(["", "## 检索与覆盖", "", "本日检索动态主题："+"、".join(refresh["searched_interests"]),
                  "查询发生变化："+("是" if audit["retrieval_queries_changed"] else "否"),
                  "范围：连接器最近聊天列表，加上持续跟踪的聊天 ID 和指定项目文件；不代表已扫描全部历史聊天。",
                  "已有主题与关键词持续保留；每日轮换仅调整额外查询的执行顺序，不重建兴趣库。",
                  "关键词：保留 "+str(len(audit.get("keyword_changes",{}).get("retained",[])))+"；新增 "+str(len(audit.get("keyword_changes",{}).get("added",[])))+"；移除 "+str(len(audit.get("keyword_changes",{}).get("removed",[])))+"。",
                  "长期兴趣、CNS 巡检、宽泛方法探索和每日 30–50 篇目标继续保留。"])
    if refresh["failed_sources"] or refresh["partial_sources"] or refresh["discovery_errors"]:
        lines.append("读取限制："+"；".join(refresh["failed_sources"]+refresh["partial_sources"]+refresh["discovery_errors"]))
    return "\n".join(lines)+"\n"


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["plan","apply"])
    parser.add_argument("--registry",type=Path,default=Path("config/ecology-context-sources.json"))
    parser.add_argument("--state-dir",type=Path,default=Path(".ecology-morning-brief-state"))
    parser.add_argument("--inventory",type=Path)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--decisions",type=Path)
    parser.add_argument("--profile",type=Path,default=Path("work/ecology-search-profile.json"))
    parser.add_argument("--date",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--changes-output",type=Path)
    args=parser.parse_args();registry=read(args.registry)
    state_path=args.state_dir/"context"/"state.json"
    state=read(state_path,{})
    if args.action=="plan":
        if not args.inventory:parser.error("plan requires --inventory")
        inventory=read(args.inventory)
        result=build_read_plan(inventory,registry,state,instant(inventory.get("checked_at",iso_now())))
        write(args.output,result)
        print(f"planned_thread_reads={len(result['thread_reads'])} deferred={len(result['deferred_thread_reads'])} files={len(result['project_files'])}")
        return 0
    if not args.input or not args.decisions:parser.error("apply requires --input and --decisions")
    bundle=collect_files(read(args.input),registry)
    previous=read(args.profile,{"schema_version":1,"retrieval_mode":"extend","evidence":{}})
    profile,state,audit=compile_refresh(previous,state,bundle,read(args.decisions),registry,args.date)
    # Keep the pre-refresh profile and append the full audit before replacing active state.
    backup=args.state_dir/"context"/"profile-history"/(digest(previous)+".json")
    if not backup.exists():write(backup,previous)
    log=args.state_dir/"context"/"changes.jsonl";log.parent.mkdir(parents=True,exist_ok=True)
    with log.open("a",encoding="utf-8") as handle:handle.write(json.dumps(audit,ensure_ascii=False)+"\n")
    write(args.output,audit)
    write(args.output.with_name(args.output.stem+"-sources.json"),bundle)
    write(args.profile,profile)
    write(state_path,state)
    if args.changes_output:
        args.changes_output.parent.mkdir(parents=True,exist_ok=True)
        args.changes_output.write_text(render_changes(audit),encoding="utf-8")
    print(f"status={audit['refresh']['status']} changes={len(audit['interest_changes'])} web_chats={audit['refresh']['web_chats_read']} files={audit['refresh']['project_files_read']}")
    return 0


if __name__=="__main__":raise SystemExit(main())
