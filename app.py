from flask import Flask, render_template, request, redirect, url_for, jsonify
import psycopg2
from datetime import datetime, timedelta

app = Flask(__name__)

# Configuração da conexão com Neon PostgreSQL
def conectar():
    return psycopg2.connect(
        "postgresql://neondb_owner:npg_cMnJsoUp74VW@ep-misty-dawn-agy72cae-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )

# Página principal: lista todos os clientes NV
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
    return render_template("painel.html", clientes=clientes)

# Prolongar licença (com input de dias)
@app.route("/prolongar/<int:cliente_id>", methods=["POST"])
def prolongar(cliente_id):
    try:
        dias = int(request.form.get("dias"))
        if dias <= 0:
            raise ValueError("Dias inválidos")
    except:
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

# Bloquear licença
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

# API para registro e sincronização dos clientes NV
@app.route("/api/licencas", methods=["GET", "POST"])
def api_licencas():
    conn = conectar()
    cur = conn.cursor()
    if request.method == "POST":
        data = request.get_json()
        cur.execute("""
            INSERT INTO clientes_nv (empresa, maquina_id, chave_licenca, data_inicio, dias, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (maquina_id) DO NOTHING
        """, (
            data["empresa"],
            data["maquina_id"],
            data["chave_licenca"],
            data["data_inicio"],
            data.get("dias", 30),
            data.get("status", "ativo")
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
                "data_inicio": str(c[3]),
                "dias": c[4],
                "status": c[5]
            })
        return jsonify(clientes_json)


if __name__ == "__main__":
    app.run(debug=True)
