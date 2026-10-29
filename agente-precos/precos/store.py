"""Persistência em SQLite: consultas, observações de ofertas (append-only), lojas e monitores."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Consulta, Oferta
from .seller import Loja
from .util import agora_brt, normalizar_url

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (id INTEGER PRIMARY KEY AUTOINCREMENT, criada_em TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ofertas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, consulta_id INTEGER NOT NULL REFERENCES consultas(id),
  url TEXT NOT NULL, url_norm TEXT NOT NULL, dominio TEXT NOT NULL, status TEXT NOT NULL,
  coletado_em TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ofertas_consulta ON ofertas(consulta_id, url_norm, id);
CREATE INDEX IF NOT EXISTS ix_ofertas_url ON ofertas(url_norm, coletado_em);
CREATE TABLE IF NOT EXISTS lojas (dominio TEXT PRIMARY KEY, atualizado_em TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monitores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, consulta_id INTEGER NOT NULL REFERENCES consultas(id),
  criado_em TEXT NOT NULL, ativo INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL);
"""


def _novo_id(cur: sqlite3.Cursor) -> int:
    if cur.lastrowid is None:
        raise RuntimeError("o banco não devolveu o id do registro inserido")
    return cur.lastrowid


class Store:
    def __init__(self, caminho: Path | str):
        self.caminho = Path(caminho)
        if str(caminho) != ":memory:":
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(caminho))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        with self._tx():
            self.db.executescript(_SCHEMA)

    @contextmanager
    def _tx(self):
        try:
            yield self.db
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def fechar(self) -> None:
        self.db.close()

    # ---------------- consultas
    def nova_consulta(self, c: Consulta) -> int:
        c.criada_em = c.criada_em or agora_brt()
        with self._tx():
            cur = self.db.execute("INSERT INTO consultas(criada_em, payload) VALUES (?, ?)", (c.criada_em.isoformat(), json.dumps(c.to_dict(), ensure_ascii=False)))
        c.id = _novo_id(cur)
        return c.id

    def consulta(self, cid: int) -> Consulta | None:
        r = self.db.execute("SELECT id, payload FROM consultas WHERE id = ?", (cid,)).fetchone()
        return None if r is None else Consulta.from_dict({**json.loads(r["payload"]), "id": r["id"]})

    def ultima_consulta_id(self) -> int | None:
        r = self.db.execute("SELECT MAX(id) AS m FROM consultas").fetchone()
        return r["m"]

    def consultas(self, limite: int = 20) -> list[Consulta]:
        rows = self.db.execute("SELECT id, payload FROM consultas ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [Consulta.from_dict({**json.loads(r["payload"]), "id": r["id"]}) for r in rows]

    # ---------------- ofertas
    def salvar_oferta(self, o: Oferta) -> int:
        if o.consulta_id is None:
            raise ValueError("oferta sem consulta_id")
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO ofertas(consulta_id, url, url_norm, dominio, status, coletado_em, payload) VALUES (?,?,?,?,?,?,?)",
                (o.consulta_id, o.url, normalizar_url(o.url), o.dominio, o.status.value, o.coletado_em.isoformat(), json.dumps(o.to_dict(), ensure_ascii=False)),
            )
        o.id = _novo_id(cur)
        return o.id

    def _oferta(self, r: sqlite3.Row) -> Oferta:
        o = Oferta.from_dict(json.loads(r["payload"]))
        o.id = r["id"]
        return o

    def oferta(self, oid: int) -> Oferta | None:
        r = self.db.execute("SELECT * FROM ofertas WHERE id = ?", (oid,)).fetchone()
        return None if r is None else self._oferta(r)

    def ultimas_ofertas(self, consulta_id: int) -> list[Oferta]:
        """Última observação de cada anúncio (URL normalizada) dentro da consulta."""
        rows = self.db.execute(
            "SELECT * FROM ofertas WHERE id IN (SELECT MAX(id) FROM ofertas WHERE consulta_id = ? GROUP BY url_norm) ORDER BY id",
            (consulta_id,),
        ).fetchall()
        return [self._oferta(r) for r in rows]

    def ultima_da_url(self, consulta_id: int, url: str) -> Oferta | None:
        r = self.db.execute("SELECT * FROM ofertas WHERE consulta_id = ? AND url_norm = ? ORDER BY id DESC LIMIT 1", (consulta_id, normalizar_url(url))).fetchone()
        return None if r is None else self._oferta(r)

    def todas_verificadas(self) -> list[tuple[int, Oferta]]:
        """(consulta_id, oferta) de todas as observações verificadas — base do histórico próprio de preços."""
        rows = self.db.execute("SELECT * FROM ofertas WHERE status = 'verificada' ORDER BY coletado_em").fetchall()
        return [(r["consulta_id"], self._oferta(r)) for r in rows]

    def historico_url(self, url: str) -> list[Oferta]:
        rows = self.db.execute("SELECT * FROM ofertas WHERE url_norm = ? AND status = 'verificada' ORDER BY coletado_em", (normalizar_url(url),)).fetchall()
        return [self._oferta(r) for r in rows]

    # ---------------- lojas
    def loja(self, dominio: str) -> Loja | None:
        r = self.db.execute("SELECT payload FROM lojas WHERE dominio = ?", (dominio,)).fetchone()
        return None if r is None else Loja.from_dict(json.loads(r["payload"]))

    def salvar_loja(self, loja: Loja) -> None:
        with self._tx():
            self.db.execute(
                "INSERT INTO lojas(dominio, atualizado_em, payload) VALUES (?,?,?) ON CONFLICT(dominio) DO UPDATE SET atualizado_em=excluded.atualizado_em, payload=excluded.payload",
                (loja.dominio, agora_brt().isoformat(), json.dumps(loja.to_dict(), ensure_ascii=False)),
            )

    def lojas(self, dominios: list[str]) -> dict[str, Loja]:
        return {d: l for d in dominios if (l := self.loja(d)) is not None}

    # ---------------- monitores
    def novo_monitor(self, consulta_id: int, cfg: dict) -> int:
        with self._tx():
            cur = self.db.execute("INSERT INTO monitores(consulta_id, criado_em, payload) VALUES (?,?,?)", (consulta_id, agora_brt().isoformat(), json.dumps(cfg, ensure_ascii=False)))
        return _novo_id(cur)

    def monitores(self, so_ativos: bool = True) -> list[dict]:
        q = "SELECT * FROM monitores" + (" WHERE ativo = 1" if so_ativos else "") + " ORDER BY id"
        return [{"id": r["id"], "consulta_id": r["consulta_id"], "criado_em": r["criado_em"], "ativo": bool(r["ativo"]), **json.loads(r["payload"])} for r in self.db.execute(q).fetchall()]

    def atualizar_monitor(self, mid: int, **campos) -> None:
        r = self.db.execute("SELECT payload FROM monitores WHERE id = ?", (mid,)).fetchone()
        if r is None:
            raise KeyError(mid)
        cfg = {**json.loads(r["payload"]), **campos}
        with self._tx():
            self.db.execute("UPDATE monitores SET payload = ? WHERE id = ?", (json.dumps(cfg, ensure_ascii=False), mid))

    def desativar_monitor(self, mid: int) -> bool:
        with self._tx():
            return self.db.execute("UPDATE monitores SET ativo = 0 WHERE id = ?", (mid,)).rowcount > 0
