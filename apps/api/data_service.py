from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .db import Database, LOCAL_USER_ID, iso_now, row_dict, utc_now
from .security import api_error


EXPORT_SCHEMA_VERSION = "1"
EXPORT_TABLES = (
    "sessions", "turns", "hint_events", "evaluations", "expressions", "review_items", "review_attempts"
)


class LearningDataService:
    def __init__(self, db: Database):
        self.db = db

    def history(self, user_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            sessions = connection.execute(
                "SELECT id,scenario_id,scenario_version,status,learning_state,started_at,ended_at,updated_at,summary_json FROM sessions WHERE user_id=? ORDER BY started_at DESC",
                (user_id,),
            ).fetchall()
            return {
                "sessions": [
                    {
                        **{k: row[k] for k in row.keys() if k != "summary_json"},
                        "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
                    }
                    for row in sessions
                ]
            }

    def export(self, user_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            data: dict[str, list[dict[str, Any]]] = {}
            data["sessions"] = [row_dict(r) for r in connection.execute("SELECT * FROM sessions WHERE user_id=?",(user_id,))]
            session_ids = [row["id"] for row in data["sessions"]]
            if session_ids:
                marks=",".join("?"*len(session_ids))
                data["turns"]=[row_dict(r) for r in connection.execute(f"SELECT * FROM turns WHERE session_id IN ({marks})",tuple(session_ids))]
            else: data["turns"]=[]
            turn_ids=[row["id"] for row in data["turns"]]
            if turn_ids:
                marks=",".join("?"*len(turn_ids))
                data["hint_events"]=[row_dict(r) for r in connection.execute(f"SELECT * FROM hint_events WHERE turn_id IN ({marks})",tuple(turn_ids))]
                data["evaluations"]=[row_dict(r) for r in connection.execute(f"SELECT * FROM evaluations WHERE turn_id IN ({marks})",tuple(turn_ids))]
            else: data["hint_events"]=[]; data["evaluations"]=[]
            data["expressions"]=[row_dict(r) for r in connection.execute("SELECT * FROM expressions WHERE user_id=?",(user_id,))]
            data["review_items"]=[row_dict(r) for r in connection.execute("SELECT * FROM review_items WHERE user_id=?",(user_id,))]
            data["review_attempts"]=[row_dict(r) for r in connection.execute("SELECT * FROM review_attempts WHERE user_id=?",(user_id,))]
        return {"schema_version":EXPORT_SCHEMA_VERSION,"export_id":str(uuid4()),"exported_at":iso_now(),"data":data}

    def preview_import(self,user_id: str,payload: dict[str,Any]) -> dict[str,Any]:
        if payload.get("schema_version") != EXPORT_SCHEMA_VERSION or not isinstance(payload.get("export_id"),str) or not isinstance(payload.get("data"),dict):
            raise api_error(422,"invalid_import","The import file schema is not supported.")
        data=payload["data"]
        if len(json.dumps(payload,ensure_ascii=False).encode("utf-8")) > 10*1024*1024: raise api_error(413,"import_too_large","The import file is larger than 10 MiB.")
        for table in EXPORT_TABLES:
            if not isinstance(data.get(table,[]),list): raise api_error(422,"invalid_import",f"Import section {table} must be a list.")
            if len(data.get(table,[])) > 100_000: raise api_error(413,"import_too_large",f"Import section {table} is too large.")
        self._validate_import_rows(data)
        counts={table:len(data.get(table,[])) for table in EXPORT_TABLES}
        preview_id=str(uuid4()); expires=(utc_now()+timedelta(minutes=10)).isoformat()
        with self.db.transaction(immediate=True) as connection:
            already=connection.execute("SELECT 1 FROM import_records WHERE user_id=? AND export_id=?",(user_id,payload["export_id"])).fetchone() is not None
            connection.execute("INSERT INTO import_previews(id,user_id,payload_json,counts_json,expires_at) VALUES (?,?,?,?,?)",(preview_id,user_id,json.dumps(payload,ensure_ascii=False),json.dumps(counts),expires))
        return {"preview_id":preview_id,"counts":counts,"warnings":["already_imported"] if already else [],"expires_at":expires}

    @staticmethod
    def _validate_import_rows(data: dict[str,Any]) -> None:
        def integer(row: dict[str,Any], table: str, field: str, minimum: int, maximum: int) -> int:
            value=row.get(field)
            if isinstance(value,bool) or not isinstance(value,int) or not minimum <= value <= maximum:
                raise api_error(422,"invalid_import",f"{table}.{field} must be an integer from {minimum} to {maximum}.")
            return value

        required={"sessions":("id","scenario_id","status"),"turns":("id","session_id","role","final_text"),"hint_events":("id","turn_id","hint_level"),"evaluations":("id","turn_id","result_json"),"expressions":("id","intent_id","canonical_form"),"review_items":("id","expression_id","due_at"),"review_attempts":("id","review_item_id","outcome")}
        ids:dict[str,set[str]]={}
        for table,fields in required.items():
            ids[table]=set()
            for row in data.get(table,[]):
                if not isinstance(row,dict): raise api_error(422,"invalid_import",f"Every {table} item must be an object.")
                if any(field not in row for field in fields): raise api_error(422,"invalid_import",f"A {table} item is missing required fields.")
                value=row.get("id")
                if not isinstance(value,str) or not value or len(value)>128 or value in ids[table]: raise api_error(422,"invalid_import",f"{table} IDs must be unique strings of at most 128 characters.")
                ids[table].add(value)
                for key,item in row.items():
                    if isinstance(item,str) and len(item)>20_000: raise api_error(422,"invalid_import",f"{table}.{key} is too long.")
                    if key.endswith("_at") and item is not None:
                        try: datetime.fromisoformat(item)
                        except (TypeError,ValueError) as exc: raise api_error(422,"invalid_import",f"{table}.{key} must be an ISO timestamp.") from exc
                    if key.endswith("_learning_date") and item is not None:
                        try: datetime.fromisoformat(item)
                        except (TypeError,ValueError) as exc: raise api_error(422,"invalid_import",f"{table}.{key} must be an ISO date.") from exc
        allowed={"sessions":{"status":{"active","paused","ended"},"learning_state":{"question","feedback","retry","paused","complete"},"media_state":{"idle","processing","error","paused","ended"}},"turns":{"role":{"assistant","user"}},"hint_events":{"hint_level":{"H1","H2","H3"}},"evaluations":{"status":{"ready","unassessable"},"outcome":{"success","needs_work","unassessable"}},"review_attempts":{"outcome":{"success","needs_work","unassessable"}}}
        for table,fields in allowed.items():
            for row in data.get(table,[]):
                for field,values in fields.items():
                    value=row.get(field)
                    if value is not None and value not in values: raise api_error(422,"invalid_import",f"{table}.{field} has an unsupported value.")
        refs=(("sessions","current_question_turn_id","turns",True),("turns","session_id","sessions",False),("turns","parent_turn_id","turns",True),("turns","question_turn_id","turns",True),("hint_events","turn_id","turns",False),("evaluations","turn_id","turns",False),("expressions","source_turn_id","turns",True),("review_items","expression_id","expressions",False),("review_attempts","review_item_id","review_items",False),("review_attempts","source_turn_id","turns",True))
        for table,field,target,optional in refs:
            for row in data.get(table,[]):
                value=row.get(field)
                if optional and value is None: continue
                if value not in ids[target]: raise api_error(422,"invalid_import",f"{table}.{field} references a missing {target} item.")
        json_fields={"sessions":{"summary_json":dict},"evaluations":{"result_json":dict},"expressions":{"acceptable_variants":list},"review_attempts":{"evaluation_json":dict}}
        for table,fields in json_fields.items():
            for row in data.get(table,[]):
                for field,expected_type in fields.items():
                    value=row.get(field)
                    if value is None: continue
                    try: parsed=json.loads(value)
                    except (TypeError,json.JSONDecodeError) as exc: raise api_error(422,"invalid_import",f"{table}.{field} must contain valid JSON.") from exc
                    if not isinstance(parsed,expected_type): raise api_error(422,"invalid_import",f"{table}.{field} has the wrong JSON shape.")
        for row in data.get("sessions",[]):
            if not isinstance(row["scenario_id"],str) or not row["scenario_id"] or len(row["scenario_id"])>128: raise api_error(422,"invalid_import","sessions.scenario_id is invalid.")
            integer(row,"sessions","scenario_version",1,1_000_000)
            integer(row,"sessions","retry_count",0,2)
            integer(row,"sessions","response_revision",0,1_000_000_000)
        for row in data.get("turns",[]):
            if not isinstance(row["final_text"],str) or len(row["final_text"])>2000: raise api_error(422,"invalid_import","Turn text must be a string of at most 2000 characters.")
            integer(row,"turns","transcript_version",1,1_000_000)
        for row in data.get("evaluations",[]):
            integer(row,"evaluations","transcript_version",1,1_000_000)
            integer(row,"evaluations","response_revision",0,1_000_000_000)
        for row in data.get("expressions",[]):
            if not isinstance(row["canonical_form"],str) or not row["canonical_form"].strip() or len(row["canonical_form"])>500: raise api_error(422,"invalid_import","Expression text must be 1-500 characters.")
            if not isinstance(row["intent_id"],str) or not row["intent_id"] or len(row["intent_id"])>128: raise api_error(422,"invalid_import","expressions.intent_id is invalid.")
            if row.get("user_edited",0) not in (0,1,False,True): raise api_error(422,"invalid_import","expressions.user_edited must be a boolean flag.")
        for row in data.get("review_items",[]):
            integer(row,"review_items","stage",0,3)
            if "attempt_count" in row: integer(row,"review_items","attempt_count",0,3)

    def apply_import(self,user_id: str,preview_id: str) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            preview=connection.execute("SELECT * FROM import_previews WHERE id=? AND user_id=?",(preview_id,user_id)).fetchone()
            if not preview or preview["expires_at"] <= iso_now(): raise api_error(410,"import_preview_expired","Create a new import preview.")
            if preview["applied_at"]: return {"imported":{table:0 for table in EXPORT_TABLES},"duplicate":True}
            payload=json.loads(preview["payload_json"]); export_id=payload["export_id"]
            if connection.execute("SELECT 1 FROM import_records WHERE user_id=? AND export_id=?",(user_id,export_id)).fetchone():
                connection.execute("UPDATE import_previews SET applied_at=? WHERE id=?",(iso_now(),preview_id)); return {"imported":{table:0 for table in EXPORT_TABLES},"duplicate":True}
            data=payload["data"]; session_map={r.get("id"):str(uuid4()) for r in data.get("sessions",[]) if isinstance(r,dict) and r.get("id")}; turn_map={r.get("id"):str(uuid4()) for r in data.get("turns",[]) if isinstance(r,dict) and r.get("id")}; expression_map={r.get("id"):str(uuid4()) for r in data.get("expressions",[]) if isinstance(r,dict) and r.get("id")}; review_map={r.get("id"):str(uuid4()) for r in data.get("review_items",[]) if isinstance(r,dict) and r.get("id")}; counts={table:0 for table in EXPORT_TABLES}
            for r in data.get("sessions",[]):
                if r.get("id") not in session_map: continue
                connection.execute("INSERT INTO sessions(id,user_id,scenario_id,scenario_version,status,learning_state,media_state,current_question_turn_id,pending_next_question,pending_next_intent_id,pending_next_task_variant,retry_count,response_revision,summary_json,started_at,ended_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_map[r["id"]],user_id,r.get("scenario_id","imported"),int(r.get("scenario_version",1)),"paused" if r.get("status")!="ended" else "ended",r.get("learning_state","question"),"paused" if r.get("status")!="ended" else "ended",turn_map.get(r.get("current_question_turn_id")),r.get("pending_next_question"),r.get("pending_next_intent_id"),r.get("pending_next_task_variant"),int(r.get("retry_count",0)),int(r.get("response_revision",0)),r.get("summary_json"),r.get("started_at",iso_now()),r.get("ended_at"),r.get("updated_at",iso_now()))); counts["sessions"]+=1
            for r in data.get("turns",[]):
                if r.get("id") not in turn_map or r.get("session_id") not in session_map: continue
                connection.execute("INSERT INTO turns(id,session_id,role,attempt_kind,parent_turn_id,question_turn_id,final_text,transcript_version,intent_id,task_variant,client_attempt_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(turn_map[r["id"]],session_map[r["session_id"]],r.get("role","user"),r.get("attempt_kind","imported"),turn_map.get(r.get("parent_turn_id")),turn_map.get(r.get("question_turn_id")),str(r.get("final_text","")),int(r.get("transcript_version",1)),r.get("intent_id"),r.get("task_variant","initial"),None,r.get("created_at",iso_now()),r.get("updated_at",iso_now()))); counts["turns"]+=1
            for r in data.get("hint_events",[]):
                if r.get("turn_id") not in turn_map: continue
                connection.execute("INSERT INTO hint_events(id,turn_id,hint_level,content_type,content,exposed_at) VALUES (?,?,?,?,?,?)",(str(uuid4()),turn_map[r["turn_id"]],r.get("hint_level","H1"),r.get("content_type","meaning_hint"),str(r.get("content","")),r.get("exposed_at",iso_now()))); counts["hint_events"]+=1
            for r in data.get("evaluations",[]):
                if r.get("turn_id") not in turn_map: continue
                connection.execute("INSERT INTO evaluations(id,turn_id,transcript_version,response_revision,schema_version,model_version,prompt_version,result_json,assistance_level,outcome,status,created_at,invalidated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid4()),turn_map[r["turn_id"]],int(r.get("transcript_version",1)),int(r.get("response_revision",0)),str(r.get("schema_version","1")),str(r.get("model_version","imported")),str(r.get("prompt_version","imported")),str(r.get("result_json","{}")),str(r.get("assistance_level","unassessable")),str(r.get("outcome","unassessable")),str(r.get("status","unassessable")),r.get("created_at",iso_now()),r.get("invalidated_at"))); counts["evaluations"]+=1
            for r in data.get("expressions",[]):
                if r.get("id") not in expression_map: continue
                inserted=connection.execute("INSERT OR IGNORE INTO expressions(id,user_id,intent_id,canonical_form,acceptable_variants,source_turn_id,user_edited,created_at) VALUES (?,?,?,?,?,?,?,?)",(expression_map[r["id"]],user_id,str(r.get("intent_id","general")),str(r.get("canonical_form","")),str(r.get("acceptable_variants","[]")),turn_map.get(r.get("source_turn_id")),int(bool(r.get("user_edited",0))),r.get("created_at",iso_now()))).rowcount
                if inserted: counts["expressions"]+=1
                else:
                    existing=connection.execute("SELECT id FROM expressions WHERE user_id=? AND intent_id=? AND canonical_form=?",(user_id,str(r.get("intent_id","general")),str(r.get("canonical_form","")))).fetchone()
                    if existing: expression_map[r["id"]]=existing["id"]
            for r in data.get("review_items",[]):
                if r.get("id") not in review_map or r.get("expression_id") not in expression_map: continue
                inserted=connection.execute("INSERT OR IGNORE INTO review_items(id,expression_id,user_id,stage,due_at,last_advanced_learning_date,created_at,current_hint_level,activity_learning_date,attempt_count) VALUES (?,?,?,?,?,?,?,?,?,?)",(review_map[r["id"]],expression_map[r["expression_id"]],user_id,int(r.get("stage",0)),r.get("due_at",iso_now()),r.get("last_advanced_learning_date"),r.get("created_at",iso_now()),"H0",r.get("activity_learning_date"),int(r.get("attempt_count",0)))).rowcount
                if inserted: counts["review_items"]+=1
                else:
                    existing=connection.execute("SELECT id FROM review_items WHERE expression_id=?",(expression_map[r["expression_id"]],)).fetchone()
                    if existing: review_map[r["id"]]=existing["id"]
            for r in data.get("review_attempts",[]):
                if r.get("review_item_id") not in review_map: continue
                connection.execute("INSERT INTO review_attempts(id,review_item_id,user_id,source_turn_id,client_attempt_id,assistance_level,outcome,task_variant,attempted_at,voided_at,transcript,evaluation_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid4()),review_map[r["review_item_id"]],user_id,turn_map.get(r.get("source_turn_id")),str(uuid4()),str(r.get("assistance_level","unassessable")),str(r.get("outcome","unassessable")),str(r.get("task_variant","review")),r.get("attempted_at",iso_now()),r.get("voided_at"),r.get("transcript"),r.get("evaluation_json"))); counts["review_attempts"]+=1
            connection.execute("INSERT INTO import_records(user_id,export_id,imported_at) VALUES (?,?,?)",(user_id,export_id,iso_now())); connection.execute("UPDATE import_previews SET applied_at=? WHERE id=?",(iso_now(),preview_id)); return {"imported":counts,"duplicate":False}

    def delete_session(self,user_id: str,session_id: str,preserve_edited: bool) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            if not connection.execute("SELECT 1 FROM sessions WHERE id=? AND user_id=?",(session_id,user_id)).fetchone(): raise api_error(404,"session_not_found","The session does not exist.")
            turn_ids=[row[0] for row in connection.execute("SELECT id FROM turns WHERE session_id=?",(session_id,)).fetchall()]
            review_ids=[row[0] for row in connection.execute("SELECT r.id FROM review_items r JOIN expressions e ON e.id=r.expression_id JOIN turns t ON t.id=e.source_turn_id WHERE t.session_id=?",(session_id,)).fetchall()]
            scopes=[f"submit:{session_id}",f"continue:{session_id}",f"skip:{session_id}",f"heartbeat:{session_id}",f"status:active:{session_id}",f"status:paused:{session_id}",f"status:ended:{session_id}"]
            scopes.extend(scope for turn_id in turn_ids for scope in (f"revise:{turn_id}",f"suspect:{turn_id}",f"hint:{turn_id}",f"alternative:{turn_id}"))
            scopes.extend(scope for review_id in review_ids for scope in (f"review-hint:{review_id}",f"review-alternative:{review_id}"))
            if scopes:
                placeholders=",".join("?" for _ in scopes)
                connection.execute(f"DELETE FROM idempotency_keys WHERE user_id=? AND (scope IN ({placeholders}) OR (json_valid(response_json) AND (json_extract(response_json,'$.id')=? OR json_extract(response_json,'$.session.id')=?)))",(user_id,*scopes,session_id,session_id))
            connection.execute("DELETE FROM import_previews WHERE user_id=?",(user_id,))
            if preserve_edited:
                connection.execute("UPDATE expressions SET source_turn_id=NULL WHERE user_id=? AND user_edited=1 AND source_turn_id IN (SELECT id FROM turns WHERE session_id=?)",(user_id,session_id))
                connection.execute("DELETE FROM expressions WHERE user_id=? AND source_turn_id IN (SELECT id FROM turns WHERE session_id=?) AND user_edited=0",(user_id,session_id))
            else:
                connection.execute("DELETE FROM expressions WHERE user_id=? AND source_turn_id IN (SELECT id FROM turns WHERE session_id=?)",(user_id,session_id))
            connection.execute("DELETE FROM sessions WHERE id=? AND user_id=?",(session_id,user_id))
        return {"deleted":True,"preserved_edited_expressions":preserve_edited}

    def delete_all(self,user_id: str) -> dict[str,Any]:
        with self.db.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM pairing_attempts")
            connection.execute("DELETE FROM users WHERE id=?",(user_id,)); now=iso_now(); connection.execute("INSERT INTO users(id,locale,timezone,created_at) VALUES (?,'ko-KR','Asia/Seoul',?)",(LOCAL_USER_ID,now)); connection.execute("INSERT INTO profiles(user_id) VALUES (?)",(LOCAL_USER_ID,))
        return {"deleted":True,"client_cache_action":"clear","external_exports_deleted":False}
