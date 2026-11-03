import pytest

from emporio.sessions import SessionStore


def test_salva_e_retoma_historico(tmp_path):
    store = SessionStore(tmp_path / "c.db")
    sid = store.new_id()
    history = [{"role": "user", "content": "Oi"}, {"role": "assistant", "content": "Olá! 🎸"}]
    store.save(sid, "ollama", history)
    store.log(sid, "user", "Oi")
    assert store.load(sid, "ollama") == history
    assert store.transcript(sid) == [("user", "Oi")]
    assert store.load("inexistente", "ollama") is None


def test_nao_mistura_provedores(tmp_path):
    store = SessionStore(tmp_path / "c.db")
    store.save("s1", "outro", [])
    with pytest.raises(ValueError):
        store.load("s1", "ollama")
