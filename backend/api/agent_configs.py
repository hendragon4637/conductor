from fastapi import APIRouter
from backend.db import queries

router = APIRouter(prefix="/api/agent_configs", tags=["agent_configs"])


@router.get("")
async def list_configs(active_only: bool = True):
    sql = "SELECT agent_config_id, cli, domain, role, pattern, active FROM agent_configs"
    if active_only:
        sql += " WHERE active"
    sql += " ORDER BY agent_config_id"
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()
