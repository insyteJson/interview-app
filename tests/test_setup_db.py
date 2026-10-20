import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import setup_db  # noqa: E402

GOOD = "postgresql://postgres.abcd1234:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"


def test_build_url_encodes_password_and_switches_driver():
    url = setup_db.build_url(GOOD, "p@ss:w/rd#1")
    assert url == ("postgresql+psycopg2://postgres.abcd1234:p%40ss%3Aw%2Frd%231"
                   "@aws-0-eu-central-1.pooler.supabase.com:5432/postgres")


@pytest.mark.parametrize("pasted, msg", [
    ("postgresql://postgres:[YOUR-PASSWORD]@db.abcd1234.supabase.co:5432/postgres", "DIRECT connection"),
    (GOOD.replace(":5432", ":6543"), "TRANSACTION pooler"),
    (GOOD.replace("postgres.abcd1234", "postgres"), "postgres.<project-ref>"),
    ("hello", "does not look like"),
])
def test_build_url_rejects_wrong_strings(pasted, msg):
    with pytest.raises(ValueError) as e:
        setup_db.build_url(pasted, "pw")
    assert msg in str(e.value)


def test_update_secrets_replaces_commented_and_old_lines():
    old = 'MODEL = "claude-opus-5-5"\n# DATABASE_URL = "postgresql+psycopg2://postgres.xxxx:PASSWORD@h:5432/postgres"\nEFFORT = "medium"\n'
    new = setup_db.update_secrets(old, "postgresql+psycopg2://u:p@h:5432/postgres")
    assert new.count("DATABASE_URL") == 1 and 'MODEL = "claude-opus-5-5"' in new and 'EFFORT = "medium"' in new
    assert new.strip().endswith('DATABASE_URL = "postgresql+psycopg2://u:p@h:5432/postgres"')


def test_region_label():
    assert setup_db.region_label("postgresql+psycopg2://u:p@aws-0-eu-west-1.pooler.supabase.com:5432/postgres") == "EU (Ireland)"
    assert setup_db.region_label("postgresql+psycopg2://u:p@aws-1-eu-central-1.pooler.supabase.com:5432/postgres") == "EU (Frankfurt)"
