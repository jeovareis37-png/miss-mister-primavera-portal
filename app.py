import os
import hashlib
import secrets
from datetime import datetime

from flask import (
    Flask, request, render_template, redirect,
    url_for, session, flash, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


# ============================================================
# BANCO DE DADOS
# ============================================================

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    def conn():
        return psycopg2.connect(
            DATABASE_URL,
            connect_timeout=10
        )

else:
    import sqlite3

    DB = os.path.join(
        os.path.dirname(__file__),
        "portal_online.db"
    )

    def conn():
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        return c


def q(c, sql, args=()):
    if DATABASE_URL:
        cur = c.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        cur.execute(
            sql.replace("?", "%s"),
            args
        )
        return cur

    return c.execute(sql, args)


# ============================================================
# CRIAÇÃO DAS TABELAS
# ============================================================

def init_db():

    c = conn()
    cur = c.cursor()

    if DATABASE_URL:

        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidatos (
            id INTEGER PRIMARY KEY,
            numero INTEGER,
            nome TEXT,
            turma TEXT,
            categoria TEXT,
            ativo INTEGER,
            codigo TEXT UNIQUE,
            senha_hash TEXT,
            portal_ativo INTEGER,
            total_votos INTEGER DEFAULT 0,
            posicao INTEGER,
            atualizado_em TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS carnes (
            id INTEGER PRIMARY KEY,
            candidato_id INTEGER,
            codigo TEXT,
            inicio_voto INTEGER,
            fim_voto INTEGER,
            status TEXT,
            votos_confirmados INTEGER DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS votos (
            numero INTEGER PRIMARY KEY,
            carne_id INTEGER,
            candidato_id INTEGER,
            pago INTEGER DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY,
            candidato_id INTEGER,
            descricao TEXT,
            criado_em TEXT
        )
        """)

    else:

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS candidatos (
            id INTEGER PRIMARY KEY,
            numero INTEGER,
            nome TEXT,
            turma TEXT,
            categoria TEXT,
            ativo INTEGER,
            codigo TEXT UNIQUE,
            senha_hash TEXT,
            portal_ativo INTEGER,
            total_votos INTEGER DEFAULT 0,
            posicao INTEGER,
            atualizado_em TEXT
        );

        CREATE TABLE IF NOT EXISTS carnes (
            id INTEGER PRIMARY KEY,
            candidato_id INTEGER,
            codigo TEXT,
            inicio_voto INTEGER,
            fim_voto INTEGER,
            status TEXT,
            votos_confirmados INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS votos (
            numero INTEGER PRIMARY KEY,
            carne_id INTEGER,
            candidato_id INTEGER,
            pago INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY,
            candidato_id INTEGER,
            descricao TEXT,
            criado_em TEXT
        );
        """)

    c.commit()
    c.close()


init_db()


# ============================================================
# TESTE DO SERVIÇO
# ============================================================

@app.get("/health")
def health():
    return jsonify(
        ok=True,
        service="Portal Miss e Mister 2026"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/", methods=["GET", "POST"])
@app.route("/portal", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        codigo = request.form.get(
            "codigo", ""
        ).strip().upper()

        senha = request.form.get(
            "senha", ""
        ).strip()

        c = conn()

        r = q(
            c,
            """
            SELECT *
            FROM candidatos
            WHERE UPPER(codigo)=?
              AND ativo=1
              AND portal_ativo=1
            """,
            (codigo,)
        ).fetchone()

        c.close()

        senha_hash = hashlib.sha256(
            ("miss-mister-2026|" + senha).encode()
        ).hexdigest()

        if r and secrets.compare_digest(
            r["senha_hash"],
            senha_hash
        ):
            session["cid"] = r["id"]
            return redirect(url_for("inicio"))

        flash("Código ou senha inválidos.")

    return render_template("login.html")


# ============================================================
# ÁREA DO CANDIDATO
# ============================================================

@app.get("/inicio")
def inicio():

    cid = session.get("cid")

    if not cid:
        return redirect(url_for("login"))

    c = conn()

    cand = q(
        c,
        "SELECT * FROM candidatos WHERE id=?",
        (cid,)
    ).fetchone()

    if not cand:
        c.close()
        session.clear()
        return redirect(url_for("login"))

    carnes = q(
        c,
        """
        SELECT *
        FROM carnes
        WHERE candidato_id=?
        ORDER BY id DESC
        """,
        (cid,)
    ).fetchall()

    votos_por = {}

    for ca in carnes:

        votos_por[ca["id"]] = q(
            c,
            """
            SELECT numero,pago
            FROM votos
            WHERE carne_id=?
            ORDER BY numero
            """,
            (ca["id"],)
        ).fetchall()

    hist = q(
        c,
        """
        SELECT *
        FROM historico
        WHERE candidato_id=?
        ORDER BY id DESC
        LIMIT 30
        """,
        (cid,)
    ).fetchall()

    ranking = q(
        c,
        """
        SELECT nome,turma,total_votos,posicao
        FROM candidatos
        WHERE ativo=1
          AND categoria=?
        ORDER BY total_votos DESC, numero
        """,
        (cand["categoria"],)
    ).fetchall()

    c.close()

    return render_template(
        "inicio.html",
        c=cand,
        carnes=carnes,
        votos_por=votos_por,
        historico=hist,
        ranking=ranking
    )


@app.get("/sair")
def sair():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# SINCRONIZAÇÃO
# ============================================================

@app.post("/api/sync")
def sync():

    token_recebido = request.headers.get(
        "X-Sync-Token", ""
    )

    if (
        not SYNC_TOKEN
        or not secrets.compare_digest(
            token_recebido,
            SYNC_TOKEN
        )
    ):
        return jsonify(
            ok=False,
            error="unauthorized"
        ), 401

    data = request.get_json(
        silent=True
    ) or {}

    candidatos = data.get(
        "candidatos", []
    )

    carnes = data.get(
        "carnes", []
    )

    votos = data.get(
        "votos", []
    )

    historico = data.get(
        "historico", []
    )

    c = None

    try:

        c = conn()
        cur = c.cursor()

        agora = datetime.now().isoformat(
            timespec="seconds"
        )

        # ====================================================
        # POSTGRESQL / SUPABASE
        # ====================================================

        if DATABASE_URL:

            cur.execute("""
            TRUNCATE TABLE
                historico,
                votos,
                carnes,
                candidatos
            """)

            if candidatos:

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO candidatos (
                        id,
                        numero,
                        nome,
                        turma,
                        categoria,
                        ativo,
                        codigo,
                        senha_hash,
                        portal_ativo,
                        total_votos,
                        posicao,
                        atualizado_em
                    )
                    VALUES %s
                    """,
                    [
                        (
                            r["id"],
                            r["numero"],
                            r["nome"],
                            r.get("turma", ""),
                            r["categoria"],
                            r.get("ativo", 1),
                            r.get("codigo"),
                            r.get("senha_hash"),
                            r.get("portal_ativo", 0),
                            r.get("total_votos", 0),
                            r.get("posicao"),
                            agora
                        )
                        for r in candidatos
                    ],
                    page_size=1000
                )

            if carnes:

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO carnes (
                        id,
                        candidato_id,
                        codigo,
                        inicio_voto,
                        fim_voto,
                        status,
                        votos_confirmados
                    )
                    VALUES %s
                    """,
                    [
                        (
                            r["id"],
                            r.get("candidato_id"),
                            r["codigo"],
                            r["inicio_voto"],
                            r["fim_voto"],
                            r["status"],
                            r.get(
                                "votos_confirmados",
                                0
                            )
                        )
                        for r in carnes
                    ],
                    page_size=1000
                )

            if votos:

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO votos (
                        numero,
                        carne_id,
                        candidato_id,
                        pago
                    )
                    VALUES %s
                    """,
                    [
                        (
                            r["numero"],
                            r["carne_id"],
                            r.get("candidato_id"),
                            r.get("pago", 0)
                        )
                        for r in votos
                    ],
                    page_size=5000
                )

            if historico:

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO historico (
                        id,
                        candidato_id,
                        descricao,
                        criado_em
                    )
                    VALUES %s
                    """,
                    [
                        (
                            r["id"],
                            r.get("candidato_id"),
                            r.get("descricao", ""),
                            r.get("criado_em", "")
                        )
                        for r in historico
                    ],
                    page_size=1000
                )

        # ====================================================
        # SQLITE LOCAL
        # ====================================================

        else:

            for tabela in (
                "historico",
                "votos",
                "carnes",
                "candidatos"
            ):
                cur.execute(
                    f"DELETE FROM {tabela}"
                )

            cur.executemany(
                """
                INSERT INTO candidatos (
                    id,
                    numero,
                    nome,
                    turma,
                    categoria,
                    ativo,
                    codigo,
                    senha_hash,
                    portal_ativo,
                    total_votos,
                    posicao,
                    atualizado_em
                )
                VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                [
                    (
                        r["id"],
                        r["numero"],
                        r["nome"],
                        r.get("turma", ""),
                        r["categoria"],
                        r.get("ativo", 1),
                        r.get("codigo"),
                        r.get("senha_hash"),
                        r.get("portal_ativo", 0),
                        r.get("total_votos", 0),
                        r.get("posicao"),
                        agora
                    )
                    for r in candidatos
                ]
            )

            cur.executemany(
                """
                INSERT INTO carnes (
                    id,
                    candidato_id,
                    codigo,
                    inicio_voto,
                    fim_voto,
                    status,
                    votos_confirmados
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                [
                    (
                        r["id"],
                        r.get("candidato_id"),
                        r["codigo"],
                        r["inicio_voto"],
                        r["fim_voto"],
                        r["status"],
                        r.get(
                            "votos_confirmados",
                            0
                        )
                    )
                    for r in carnes
                ]
            )

            cur.executemany(
                """
                INSERT INTO votos (
                    numero,
                    carne_id,
                    candidato_id,
                    pago
                )
                VALUES (?,?,?,?)
                """,
                [
                    (
                        r["numero"],
                        r["carne_id"],
                        r.get("candidato_id"),
                        r.get("pago", 0)
                    )
                    for r in votos
                ]
            )

            cur.executemany(
                """
                INSERT INTO historico (
                    id,
                    candidato_id,
                    descricao,
                    criado_em
                )
                VALUES (?,?,?,?)
                """,
                [
                    (
                        r["id"],
                        r.get("candidato_id"),
                        r.get("descricao", ""),
                        r.get("criado_em", "")
                    )
                    for r in historico
                ]
            )

        c.commit()

        return jsonify(
            ok=True,
            candidatos=len(candidatos),
            carnes=len(carnes),
            votos=len(votos),
            historico=len(historico),
            atualizado_em=agora
        )

    except Exception as e:

        if c:

            try:
                c.rollback()
            except Exception:
                pass

        app.logger.exception(
            "Erro na sincronizacao do portal"
        )

        return jsonify(
            ok=False,
            error="sync_failed",
            detail=str(e)
        ), 500

    finally:

        if c:

            try:
                c.close()
            except Exception:
                pass


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "8080"
            )
        )
    )
