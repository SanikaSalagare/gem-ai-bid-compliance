#!/usr/bin/env python
"""Django's command-line utility, kept separate from manage.py.

manage.py in this project is already your backend module (create_tender,
list_tenders, process_bid, etc. — imported elsewhere as `import manage`),
so it can't also be Django's admin entrypoint. This file is the Django
entrypoint instead. Usage is identical to a normal Django manage.py:

    python runserver.py runserver
    python runserver.py runserver 0.0.0.0:8000
    python runserver.py migrate
    python runserver.py shell
"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sih.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Make sure it's installed and "
            "available on your PYTHONPATH environment variable. Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()