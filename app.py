import os
import csv
from io import StringIO

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json, RealDictCursor

load_dotenv(override=True)

app = FastAPI()

load_dotenv(override=True)

app = FastAPI()
DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS webhook_raw (
            id BIGSERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS pr_metrics (
            id BIGSERIAL PRIMARY KEY,
            raw_id BIGINT REFERENCES webhook_raw(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            project_name TEXT,
            repository_name TEXT,
            pull_request_id BIGINT,
            actor_name TEXT,
            status TEXT,
            created_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pr_metrics_pull_request_id
        ON pr_metrics (pull_request_id);
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_pr_metrics_received_at
        ON pr_metrics (received_at DESC);
        """)

        cur.execute("""
        CREATE OR REPLACE VIEW vw_pr_latest AS
        SELECT DISTINCT ON (pull_request_id)
            id,
            raw_id,
            event_type,
            project_name,
            repository_name,
            pull_request_id,
            actor_name,
            status,
            created_at,
            closed_at,
            received_at
        FROM pr_metrics
        WHERE pull_request_id IS NOT NULL
        ORDER BY pull_request_id, received_at DESC, id DESC;
        """)

        conn.commit()
        print("Tablas y vista creadas/verificadas correctamente.")

    except Exception as e:
        if conn:
            conn.rollback()
        print("ERROR init_db:", repr(e))
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {
        "ok": True,
        "message": "API funcionando",
        "health": "/health",
        "webhook": "/webhooks/azure-devops",
        "dashboard": "/dashboard",
        "api_dashboard": "/api/dashboard-data",
        "csv_export": "/api/pr-export.csv"
    }


@app.get("/health")
def health():
    conn = None
    try:
        conn = get_conn()
        return {"ok": True, "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")
    finally:
        if conn:
            conn.close()


@app.post("/webhooks/azure-devops")
async def webhook(request: Request):
    conn = None
    cur = None

    try:
        payload = await request.json()
        event_type = payload.get("eventType", "unknown")
        resource = payload.get("resource", {}) or {}

        project_name = (
            resource.get("repository", {}).get("project", {}).get("name")
            or resource.get("project", {}).get("name")
            or ""
        )
        repository_name = resource.get("repository", {}).get("name")
        pull_request_id = resource.get("pullRequestId")
        actor_name = resource.get("createdBy", {}).get("displayName")
        status = resource.get("status")
        created_at = resource.get("creationDate")
        closed_at = resource.get("closedDate")

        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO webhook_raw (event_type, payload)
            VALUES (%s, %s)
            RETURNING id
            """,
            (event_type, Json(payload))
        )
        raw_id = cur.fetchone()[0]
        print(f"Insert webhook_raw OK. raw_id={raw_id}")

        if event_type.startswith("git.pullrequest"):
            cur.execute(
                """
                INSERT INTO pr_metrics (
                    raw_id,
                    event_type,
                    project_name,
                    repository_name,
                    pull_request_id,
                    actor_name,
                    status,
                    created_at,
                    closed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    raw_id,
                    event_type,
                    project_name,
                    repository_name,
                    pull_request_id,
                    actor_name,
                    status,
                    created_at,
                    closed_at
                )
            )
            pr_id = cur.fetchone()[0]
            print(f"Insert pr_metrics OK. id={pr_id}")
        else:
            print(f"Evento no PR, solo guardado en webhook_raw: {event_type}")

        conn.commit()

        return {
            "ok": True,
            "raw_id": raw_id,
            "event_type": event_type
        }

    except Exception as e:
        if conn:
            conn.rollback()
        print("ERROR insert:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.get("/api/dashboard-data")
def api_dashboard_data():
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                COUNT(*) AS total_pr,
                COUNT(*) FILTER (WHERE status = 'active') AS active_pr,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed_pr,
                ROUND(
                    AVG(EXTRACT(EPOCH FROM (closed_at - created_at)) / 60.0)
                    FILTER (WHERE created_at IS NOT NULL AND closed_at IS NOT NULL),
                    2
                ) AS avg_minutes
            FROM vw_pr_latest
        """)
        summary = cur.fetchone()

        cur.execute("""
            SELECT
                id,
                event_type,
                project_name,
                repository_name,
                pull_request_id,
                actor_name,
                status,
                created_at,
                closed_at,
                received_at,
                CASE
                    WHEN created_at IS NOT NULL AND closed_at IS NOT NULL
                    THEN ROUND(EXTRACT(EPOCH FROM (closed_at - created_at)) / 60.0, 2)
                    ELSE NULL
                END AS duration_minutes
            FROM vw_pr_latest
            ORDER BY received_at DESC, id DESC
            LIMIT 200
        """)
        rows = cur.fetchall()

        cur.execute("""
            SELECT
                COALESCE(status, 'unknown') AS status,
                COUNT(*) AS total
            FROM vw_pr_latest
            GROUP BY COALESCE(status, 'unknown')
            ORDER BY total DESC
        """)
        status_chart = cur.fetchall()

        cur.execute("""
            SELECT
                COALESCE(actor_name, 'Sin nombre') AS actor_name,
                COUNT(*) AS total
            FROM vw_pr_latest
            GROUP BY COALESCE(actor_name, 'Sin nombre')
            ORDER BY total DESC
            LIMIT 10
        """)
        actor_chart = cur.fetchall()

        return {
            "summary": {
                "total_pr": summary["total_pr"] or 0,
                "active_pr": summary["active_pr"] or 0,
                "completed_pr": summary["completed_pr"] or 0,
                "avg_minutes": summary["avg_minutes"] or 0
            },
            "status_chart": status_chart,
            "actor_chart": actor_chart,
            "rows": rows
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.get("/api/pr-export.csv")
def export_csv():
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                pull_request_id,
                project_name,
                repository_name,
                actor_name,
                status,
                created_at,
                closed_at,
                received_at,
                CASE
                    WHEN created_at IS NOT NULL AND closed_at IS NOT NULL
                    THEN ROUND(EXTRACT(EPOCH FROM (closed_at - created_at)) / 60.0, 2)
                    ELSE NULL
                END AS duration_minutes
            FROM vw_pr_latest
            ORDER BY received_at DESC, id DESC
        """)
        rows = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "pull_request_id",
            "project_name",
            "repository_name",
            "actor_name",
            "status",
            "created_at",
            "closed_at",
            "received_at",
            "duration_minutes"
        ])

        for row in rows:
            writer.writerow([
                row["pull_request_id"],
                row["project_name"],
                row["repository_name"],
                row["actor_name"],
                row["status"],
                row["created_at"],
                row["closed_at"],
                row["received_at"],
                row["duration_minutes"]
            ])

        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=pr_dashboard_export.csv"}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Dashboard Azure DevOps</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      padding: 24px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 24px;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }
    .card, .panel, .table-wrap {
      background: #1e293b;
      border-radius: 16px;
      padding: 20px;
    }
    .label {
      color: #94a3b8;
      font-size: 14px;
      margin-bottom: 8px;
    }
    .value {
      font-size: 30px;
      font-weight: 700;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }
    button, a.btn {
      background: #2563eb;
      color: white;
      border: none;
      text-decoration: none;
      padding: 12px 16px;
      border-radius: 10px;
      cursor: pointer;
      font-weight: 600;
      display: inline-block;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1000px;
    }
    th, td {
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid #334155;
      font-size: 14px;
    }
    th {
      color: #93c5fd;
    }
    .badge {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
    }
    .active { background: #7c2d12; color: #fdba74; }
    .completed { background: #14532d; color: #86efac; }
    .other { background: #334155; color: #cbd5e1; }

    @media (max-width: 1000px) {
      .cards { grid-template-columns: repeat(2, 1fr); }
      .grid { grid-template-columns: 1fr; }
    }

    @media (max-width: 640px) {
      .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <div>
      <h1 style="margin:0;">Dashboard Pull Requests</h1>
      <div style="color:#94a3b8;">Vista por PR único y descarga CSV</div>
    </div>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <button onclick="loadData()">Actualizar</button>
      <a class="btn" href="/api/pr-export.csv">Descargar CSV</a>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <div class="label">Total PR únicos</div>
      <div class="value" id="total_pr">0</div>
    </div>
    <div class="card">
      <div class="label">PR activos</div>
      <div class="value" id="active_pr">0</div>
    </div>
    <div class="card">
      <div class="label">PR completados</div>
      <div class="value" id="completed_pr">0</div>
    </div>
    <div class="card">
      <div class="label">Promedio minutos</div>
      <div class="value" id="avg_minutes">0</div>
    </div>
  </div>

  <div class="grid">
    <div class="panel">
      <h3 style="margin-top:0;">PR por estado</h3>
      <canvas id="statusChart"></canvas>
    </div>
    <div class="panel">
      <h3 style="margin-top:0;">Top usuarios</h3>
      <canvas id="actorChart"></canvas>
    </div>
  </div>

  <div class="table-wrap">
    <h3 style="margin-top:0;">Últimos PR únicos</h3>
    <table>
      <thead>
        <tr>
          <th>ID PR</th>
          <th>Proyecto</th>
          <th>Repositorio</th>
          <th>Usuario</th>
          <th>Estado</th>
          <th>Creado</th>
          <th>Cerrado</th>
          <th>Duración (min)</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <script>
    let statusChart;
    let actorChart;

    function formatDate(v) {
      if (!v) return "-";
      return new Date(v).toLocaleString();
    }

    function badgeClass(status) {
      if (status === "active") return "badge active";
      if (status === "completed") return "badge completed";
      return "badge other";
    }

    async function loadData() {
      const res = await fetch('/api/dashboard-data');
      const data = await res.json();

      document.getElementById('total_pr').textContent = data.summary.total_pr;
      document.getElementById('active_pr').textContent = data.summary.active_pr;
      document.getElementById('completed_pr').textContent = data.summary.completed_pr;
      document.getElementById('avg_minutes').textContent = data.summary.avg_minutes;

      const tbody = document.getElementById('tbody');
      tbody.innerHTML = '';

      data.rows.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${row.pull_request_id ?? '-'}</td>
          <td>${row.project_name ?? '-'}</td>
          <td>${row.repository_name ?? '-'}</td>
          <td>${row.actor_name ?? '-'}</td>
          <td><span class="${badgeClass(row.status)}">${row.status ?? '-'}</span></td>
          <td>${formatDate(row.created_at)}</td>
          <td>${formatDate(row.closed_at)}</td>
          <td>${row.duration_minutes ?? '-'}</td>
        `;
        tbody.appendChild(tr);
      });

      const statusLabels = data.status_chart.map(x => x.status);
      const statusValues = data.status_chart.map(x => x.total);

      const actorLabels = data.actor_chart.map(x => x.actor_name);
      const actorValues = data.actor_chart.map(x => x.total);

      if (statusChart) statusChart.destroy();
      if (actorChart) actorChart.destroy();

      statusChart = new Chart(document.getElementById('statusChart'), {
        type: 'bar',
        data: {
          labels: statusLabels,
          datasets: [{
            label: 'Cantidad',
            data: statusValues
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } }
        }
      });

      actorChart = new Chart(document.getElementById('actorChart'), {
        type: 'doughnut',
        data: {
          labels: actorLabels,
          datasets: [{
            label: 'PR',
            data: actorValues
          }]
        },
        options: { responsive: true }
      });
    }

    loadData();
  </script>
</body>
</html>
    """
