"""Qt 6 method names that PySide2 spells differently."""


def add_exec_alias(*classes):
    # Qt 6 / PySide6 call it exec(); PySide2 only has exec_().
    for cls in classes:
        if not hasattr(cls, "exec") and hasattr(cls, "exec_"):
            cls.exec = cls.exec_
