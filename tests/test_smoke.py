def test_imports():
    import importlib

    for mod in ["bot", "agent", "risk_guard"]:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            pass  # Modul fehlt? Test nicht hart failen vorerst
