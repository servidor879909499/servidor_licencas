from flask import Flask, render_template, request, redirect, url_for, jsonify
import psycopg2
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# ======== CONEXÃO COM O BANCO (Neon PostgreSQL) ========
def conectar():
    return psycopg2.connect(
        "postgresql://neondb_owner:npg_cMnJsoUp74VW@ep-misty-dawn-agy72cae-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )

# ======== GARANTIR QUE A TABELA EXISTE ========
def criar_tabela_clientes_nv():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes_nv (
            id SERIAL PRIMARY KEY,
            empresa TEXT,
            maquina_id TEXT UNIQUE,
            chave_licenca TEXT,
            data_inicio TIMESTAMP,
            dias INTEGER,
            status TEXT,
            ultima_sync TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

criar_tabela_clientes_nv()

# ======== PÁGINA PRINCIPAL ========
@app.route("/")
@app.route("/painel")
def painel():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync
        FROM clientes_nv
        ORDER BY empresa
    """)
    clientes = cur.fetchall()
    conn.close()

    # Calcula data_fim para cada cliente
    clientes_final = []
    for c in clientes:
        data_inicio = c[4]
        dias = c[5] or 0
        data_fim = (data_inicio + timedelta(days=dias)) if data_inicio else None
        clientes_final.append(list(c) + [data_fim])  # adiciona como c[8]

    return render_template("painel.html", clientes=clientes_final)

# ======== PROLONGAR LICENÇA ========
@app.route("/prolongar/<int:cliente_id>", methods=["POST"])
def prolongar(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias <= 0:
        return redirect(url_for("painel"))

    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clientes_nv
        SET dias = dias + %s, status='ativo', ultima_sync=%s
        WHERE id=%s
    """, (dias, datetime.now(), cliente_id))
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

# ======== DIMINUIR LICENÇA ========
@app.route("/diminuir/<int:cliente_id>", methods=["POST"])
def diminuir(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias <= 0:
        return redirect(url_for("painel"))

    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clientes_nv
        SET dias = GREATEST(dias - %s, 0), ultima_sync=%s
        WHERE id=%s
    """, (dias, datetime.now(), cliente_id))
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

# ======== BLOQUEAR LICENÇA ========
@app.route("/bloquear/<int:cliente_id>", methods=["POST"])
def bloquear(cliente_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clientes_nv
        SET status='bloqueado', ultima_sync=%s
        WHERE id=%s
    """, (datetime.now(), cliente_id))
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

# ======== API LICENÇAS ========
@app.route("/api/licencas", methods=["GET", "POST"], strict_slashes=False)
def api_licencas():
    conn = conectar()
    cur = conn.cursor()

    if request.method == "POST":
        data = request.get_json()
        cur.execute("""
            INSERT INTO clientes_nv (empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (maquina_id)
            DO UPDATE SET empresa=EXCLUDED.empresa,
                          chave_licenca=EXCLUDED.chave_licenca,
                          dias=EXCLUDED.dias,
                          status=EXCLUDED.status,
                          ultima_sync=EXCLUDED.ultima_sync
        """, (
            data["empresa"],
            data["maquina_id"],
            data["chave_licenca"],
            datetime.strptime(data["data_inicio"], "%Y-%m-%d %H:%M:%S"),
            data.get("dias", 30),
            data.get("status", "ativo"),
            datetime.now()
        ))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    else:  # GET
        cur.execute("SELECT empresa, maquina_id, chave_licenca, data_inicio, dias, status FROM clientes_nv")
        clientes = cur.fetchall()
        conn.close()
        clientes_json = []
        for c in clientes:
            clientes_json.append({
                "empresa": c[0],
                "maquina_id": c[1],
                "chave_licenca": c[2],
                "data_inicio": c[3].strftime("%Y-%m-%d %H:%M:%S"),
                "dias": c[4],
                "status": c[5]
            })
        return jsonify(clientes_json)

# ======== INÍCIO DO SERVIDOR ========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
