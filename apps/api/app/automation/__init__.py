"""Runtime d'automatisation (Phase 7) — la coquille qui accueille les modules métier.

Ce package est l'UNIQUE point de couture entre le cœur et les modules
(`app/modules/<name>/`). Écrit une fois, il n'est jamais modifié par l'ajout d'un
module : un module = un package sous `app/modules/`, une ligne dans `registry.py`,
une migration tenant (invariant de phase n°1, garanti par `test_module_isolation`).
"""
