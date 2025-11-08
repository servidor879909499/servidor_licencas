from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import psycopg2
from datetime import datetime, timedelta
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rcanvas
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = "sua_chave_secreta_aqui"  # altere para algo seguro em produção

# ======== CONEXÃO COM O BANCO ========
def conectar():
    return psycopg2.connect(
        "postgresql://neondb_owner:npg_cMnJsoUp74VW@ep-misty-dawn-agy72cae-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    )

# ======== CRIAÇÃO/MIGRAÇÕES LEVES ========
def criar_tabelas_essenciais():
    conn = conectar()
    cur = conn.cursor()
    # tabela clientes_nv (se não existir)
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
    # tenta adicionar coluna email, se não existir
    try:
        cur.execute("ALTER TABLE clientes_nv ADD COLUMN email TEXT")
    except Exception:
        conn.rollback()
    # configuracoes (chave, valor)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            id SERIAL PRIMARY KEY,
            chave TEXT UNIQUE,
            valor TEXT
        )
    """)
    # faturas agendadas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faturas_agendadas (
            id SERIAL PRIMARY KEY,
            cliente_id INTEGER REFERENCES clientes_nv(id) ON DELETE CASCADE,
            email_cliente TEXT,
            valor NUMERIC,
            dia_emissao DATE,
            proxima_envio TIMESTAMP,
            ativo BOOLEAN DEFAULT TRUE,
            criado_em TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    conn.close()

criar_tabelas_essenciais()

# ======== UTILITÁRIOS DE CONFIG ========
def get_config(chave, default=None):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT valor FROM configuracoes WHERE chave = %s", (chave,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def set_config(chave, valor):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO configuracoes (chave, valor) VALUES (%s, %s)
        ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor
    """, (chave, valor))
    conn.commit()
    conn.close()

# ======== ROTEAMENTO PRINCIPAL (painel) ========
@app.route("/")
@app.route("/painel")
def painel():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync, email
        FROM clientes_nv
        ORDER BY empresa
    """)
    clientes = cur.fetchall()
    conn.close()

    clientes_final = []
    for c in clientes:
        data_inicio = c[4]
        dias = c[5] or 0
        data_fim = data_inicio + timedelta(days=dias) if data_inicio else None
        clientes_final.append(list(c) + [data_fim])

    return render_template("painel.html", clientes=clientes_final, title="Painel de Licenças NV Sistema")

# ======== API: LISTAR LICENÇAS ========
@app.route("/api/licencas", methods=["GET"])
def listar_licencas():
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT empresa, maquina_id, chave_licenca, data_inicio, dias, status, email
        FROM clientes_nv
    """)
    licencas = cur.fetchall()
    conn.close()

    lista = []
    for l in licencas:
        lista.append({
            "empresa": l[0],
            "maquina_id": l[1],
            "chave_licenca": l[2],
            "data_inicio": l[3].strftime("%Y-%m-%d %H:%M:%S") if l[3] else None,
            "dias": l[4],
            "status": l[5],
            "email": l[6]
        })
    return jsonify(lista)

# ======== FUNÇÕES DE LICENÇA (atualizar) ========
def atualizar_cliente(cliente_id, dias_delta=0, status=None, action=None):
    conn = conectar()
    cur = conn.cursor()
    if action == "prolongar":
        cur.execute("""
            UPDATE clientes_nv
            SET dias = dias + %s, status='ativo', ultima_sync=%s
            WHERE id=%s
        """, (dias_delta, datetime.now(), cliente_id))
        flash(f"Licença prolongada em {dias_delta} dias!", "success")
    elif action == "diminuir":
        cur.execute("""
            UPDATE clientes_nv
            SET dias = GREATEST(dias - %s, 0), ultima_sync=%s
            WHERE id=%s
        """, (dias_delta, datetime.now(), cliente_id))
        flash(f"Licença diminuída em {dias_delta} dias!", "warning")
    elif action == "bloquear":
        cur.execute("""
            UPDATE clientes_nv
            SET status='bloqueado', ultima_sync=%s
            WHERE id=%s
        """, (datetime.now(), cliente_id))
        flash("Licença bloqueada!", "danger")
    conn.commit()
    conn.close()

@app.route("/prolongar/<int:cliente_id>", methods=["POST"])
def prolongar(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias > 0:
        atualizar_cliente(cliente_id, dias, action="prolongar")
    return redirect(url_for("painel"))

@app.route("/diminuir/<int:cliente_id>", methods=["POST"])
def diminuir(cliente_id):
    dias = int(request.form.get("dias", 0))
    if dias > 0:
        atualizar_cliente(cliente_id, dias, action="diminuir")
    return redirect(url_for("painel"))

@app.route("/bloquear/<int:cliente_id>", methods=["POST"])
def bloquear(cliente_id):
    atualizar_cliente(cliente_id, action="bloquear")
    return redirect(url_for("painel"))

# ======== REMOVER EMPRESA ========
@app.route("/remover/<int:cliente_id>", methods=["POST"])
def remover(cliente_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("DELETE FROM clientes_nv WHERE id = %s", (cliente_id,))
    conn.commit()
    conn.close()
    flash("Empresa removida com sucesso!", "success")
    return redirect(url_for("painel"))

# ======== ROTAS: CONFIGURAÇÕES (empresa + smtp) ========
@app.route("/configuracoes", methods=["GET", "POST"])
def configuracoes():
    if request.method == "POST":
        set_config("empresa_nome", request.form.get("empresa_nome", "B&N SERVICOS LDA"))
        set_config("empresa_nuit", request.form.get("empresa_nuit", ""))
        set_config("empresa_email", request.form.get("empresa_email", "bnsevicoslda@gmail.com"))
        set_config("empresa_telefone", request.form.get("empresa_telefone", "+258 844 648 689; +258 879 909 499"))
        # SMTP
        set_config("smtp_host", request.form.get("smtp_host", "smtp.gmail.com"))
        set_config("smtp_port", request.form.get("smtp_port", "587"))
        set_config("smtp_user", request.form.get("smtp_user", ""))
        set_config("smtp_pass", request.form.get("smtp_pass", ""))
        flash("Configurações salvas.", "success")
        return redirect(url_for("configuracoes"))
    configs = {
        "empresa_nome": get_config("empresa_nome", "B&N SERVICOS LDA"),
        "empresa_nuit": get_config("empresa_nuit", ""),
        "empresa_email": get_config("empresa_email", "bnsevicoslda@gmail.com"),
        "empresa_telefone": get_config("empresa_telefone", "+258 844 648 689; +258 879 909 499"),
        "smtp_host": get_config("smtp_host", "smtp.gmail.com"),
        "smtp_port": get_config("smtp_port", "587"),
        "smtp_user": get_config("smtp_user", ""),
        "smtp_pass": get_config("smtp_pass", "")
    }
    return render_template("configuracoes.html", configs=configs)

# ======== ROTAS: Faturas ========
@app.route("/faturas")
def faturas():
    # lista faturas agendadas e clientes
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT f.id, f.cliente_id, COALESCE(c.empresa, '') as empresa, f.email_cliente, f.valor, f.dia_emissao, f.proxima_envio, f.ativo
        FROM faturas_agendadas f
        LEFT JOIN clientes_nv c ON c.id = f.cliente_id
        ORDER BY f.proxima_envio
    """)
    rows = cur.fetchall()
    # lista de clientes para select
    cur.execute("SELECT id, empresa, email FROM clientes_nv ORDER BY empresa")
    clientes = cur.fetchall()
    conn.close()
    return render_template("faturas.html", faturas=rows, clientes=clientes)

@app.route("/faturas/agendar", methods=["POST"])
def agendar_fatura():
    cliente_id = request.form.get("cliente_id")
    valor = request.form.get("valor")
    dia_emissao = request.form.get("dia_emissao")
    email_cliente_form = request.form.get("email_cliente", None)

    if not cliente_id or not valor or not dia_emissao:
        flash("Preencha todos os campos para agendar a fatura.", "warning")
        return redirect(url_for("faturas"))

    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT email FROM clientes_nv WHERE id = %s", (cliente_id,))
    row = cur.fetchone()
    if row and row[0]:
        email_cliente = row[0]
    else:
        email_cliente = email_cliente_form

    try:
        proxima = datetime.strptime(dia_emissao, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    except Exception:
        flash("Formato de data inválido. Use YYYY-MM-DD.", "warning")
        conn.close()
        return redirect(url_for("faturas"))

    cur.execute("""
        INSERT INTO faturas_agendadas (cliente_id, email_cliente, valor, dia_emissao, proxima_envio, ativo)
        VALUES (%s, %s, %s, %s, %s, TRUE)
    """, (cliente_id, email_cliente, valor, dia_emissao, proxima))
    conn.commit()
    conn.close()
    flash("Fatura agendada com sucesso.", "success")
    return redirect(url_for("faturas"))

@app.route("/faturas/cancelar/<int:fatura_id>", methods=["POST"])
def cancelar_fatura(fatura_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("UPDATE faturas_agendadas SET ativo = FALSE WHERE id = %s", (fatura_id,))
    conn.commit()
    conn.close()
    flash("Fatura agendada cancelada.", "info")
    return redirect(url_for("faturas"))

# ======== API REGISTRO AUTOMÁTICO (recebe licenças) ========
@app.route("/api/licencas", methods=["POST"])
def api_licencas():
    data = request.get_json()
    conn = conectar()
    cur = conn.cursor()
    email = data.get("email")
    data_inicio = None
    try:
        if data.get("data_inicio"):
            data_inicio = datetime.strptime(data["data_inicio"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        data_inicio = None

    cur.execute("""
        INSERT INTO clientes_nv (empresa, maquina_id, chave_licenca, data_inicio, dias, status, ultima_sync, email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (maquina_id)
        DO UPDATE SET empresa=EXCLUDED.empresa, chave_licenca=EXCLUDED.chave_licenca,
                      dias=EXCLUDED.dias, status=EXCLUDED.status, ultima_sync=EXCLUDED.ultima_sync,
                      email=COALESCE(EXCLUDED.email, clientes_nv.email)
    """, (
        data.get("empresa"),
        data.get("maquina_id"),
        data.get("chave_licenca"),
        data_inicio,
        data.get("dias", 30),
        data.get("status", "ativo"),
        datetime.now(),
        email
    ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ======== API GET para licença por maquina_id ========
@app.route("/api/licencas/<maquina_id>", methods=["GET"])
def buscar_licenca(maquina_id):
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT empresa, maquina_id, chave_licenca, data_inicio, dias, status, email
        FROM clientes_nv
        WHERE maquina_id = %s
    """, (maquina_id,))
    licenca = cur.fetchone()
    conn.close()

    if licenca:
        return jsonify({
            "empresa": licenca[0],
            "maquina_id": licenca[1],
            "chave_licenca": licenca[2],
            "data_inicio": licenca[3].strftime("%Y-%m-%d %H:%M:%S") if licenca[3] else None,
            "dias": licenca[4],
            "status": licenca[5],
            "email": licenca[6]
        })
    return jsonify({"error": "Licença não encontrada"}), 404

# ======== GERAÇÃO DE PDF E ENVIO DE EMAIL ========
def gerar_pdf_fatura(empresa_info, cliente_info, valor, referencia):
    buffer = io.BytesIO()
    p = rcanvas(buffer, pagesize=A4)
    width, height = A4

    p.setFont("Helvetica-Bold", 16)
    p.drawString(40, height - 80, empresa_info.get("nome", "B&N SERVICOS LDA"))
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 100, f"NUIT: {empresa_info.get('nuit', '')}")
    p.drawString(40, height - 115, f"Email: {empresa_info.get('email', '')}")
    p.drawString(40, height - 130, f"Telefone: {empresa_info.get('telefone', '')}")

    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, height - 170, "Fatura de Cobrança")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 190, f"Referência: {referencia}")
    p.drawString(40, height - 205, f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, height - 240, "Cliente:")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 255, f"Nome/Empresa: {cliente_info.get('empresa', '')}")
    p.drawString(40, height - 270, f"Email: {cliente_info.get('email', '')}")

    p.setFont("Helvetica-Bold", 11)
    p.drawString(40, height - 300, "Descrição")
    p.drawString(400, height - 300, "Valor (MZN)")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 320, "Serviço Mensal")
    p.drawString(400, height - 320, f"{valor:.2f}")

    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, height - 360, f"Total: {valor:.2f} MZN")

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer.read()

def enviar_email_com_anexo(destinatario, assunto, corpo_html, anexo_bytes, anexo_nome):
    smtp_host = get_config("smtp_host", "smtp.gmail.com")
    smtp_port = int(get_config("smtp_port", "587"))
    smtp_user = get_config("smtp_user", "")
    smtp_pass = get_config("smtp_pass", "")

    if not smtp_user or not smtp_pass:
        app.logger.error("SMTP não configurado corretamente.")
        return False

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_html, "html"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(anexo_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{anexo_nome}"')
    msg.attach(part)

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [destinatario], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        app.logger.exception("Erro ao enviar e-mail: %s", e)
        return False

# ======== JOB: verificar faturas e enviar ========
def verificar_e_enviar_faturas():
    app.logger.info("Verificando faturas agendadas para envio...")
    conn = conectar()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, cliente_id, email_cliente, valor, proxima_envio
        FROM faturas_agendadas
        WHERE ativo = TRUE AND proxima_envio <= %s
        ORDER BY proxima_envio
    """, (datetime.now(),))
    rows = cur.fetchall()
    for row in rows:
        f_id, cliente_id, email_cliente, valor, proxima_envio = row
        # buscar cliente
        cur.execute("SELECT empresa, email FROM clientes_nv WHERE id = %s", (cliente_id,))
        cliente = cur.fetchone()
        cliente_nome = cliente[0] if cliente else "Cliente"
        cliente_email = cliente[1] or email_cliente
        # empresa
        empresa_info = {
            "nome": get_config("empresa_nome", "B&N SERVICOS LDA"),
            "nuit": get_config("empresa_nuit", ""),
            "email": get_config("empresa_email", "bnsevicoslda@gmail.com"),
            "telefone": get_config("empresa_telefone", "+258 844 648 689; +258 879 909 499")
        }
        cliente_info = {"empresa": cliente_nome, "email": cliente_email}
        referencia = f"FAT-{f_id}-{proxima_envio.strftime('%Y%m%d')}"
        # gerar pdf
        try:
            pdf_bytes = gerar_pdf_fatura(empresa_info, cliente_info, float(valor), referencia)
        except Exception as e:
            app.logger.exception("Erro gerando PDF: %s", e)
            continue
        corpo = f"""
            <p>Olá {cliente_nome},</p>
            <p>Segue anexo a fatura de cobrança referente ao serviço mensal. Valor: <strong>{float(valor):.2f} MZN</strong>.</p>
            <p>Atenciosamente,<br>{empresa_info['nome']}</p>
        """
        if cliente_email:
            enviado = enviar_email_com_anexo(cliente_email, f"Fatura - {empresa_info['nome']}", corpo, pdf_bytes, f"{referencia}.pdf")
            if enviado:
                app.logger.info("Fatura %s enviada para %s", f_id, cliente_email)
                # adicionar 1 mês simples (30 dias) para próxima
                proxima_nova = proxima_envio + timedelta(days=30)
                cur.execute("UPDATE faturas_agendadas SET proxima_envio = %s WHERE id = %s", (proxima_nova, f_id))
                conn.commit()
            else:
                app.logger.error("Falha ao enviar fatura %s para %s", f_id, cliente_email)
        else:
            app.logger.warning("Fatura %s sem email do cliente (id=%s)", f_id, cliente_id)
    conn.close()

# inicializa scheduler (checa a cada 60s - bom para testes; em produção use cron/worker separado)
scheduler = BackgroundScheduler()
scheduler.add_job(func=verificar_e_enviar_faturas, trigger="interval", seconds=60)
scheduler.start()

# ======== ROTAS TEMPORÁRIAS MANTIDAS ========
@app.route("/licencas")
def licencas():
    return "<h1>Licenças - Em desenvolvimento</h1>"

@app.route("/clientes")
def clientes():
    return "<h1>Clientes - Em desenvolvimento</h1>"

@app.route("/atualizacoes")
def atualizacoes():
    return "<h1>Atualizações - Em desenvolvimento</h1>"

# ======== LOGOUT ========
@app.route("/logout")
def logout():
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("painel"))

# ======== FINAL ========
if __name__ == "__main__":
    try:
        app.run(debug=True)
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
