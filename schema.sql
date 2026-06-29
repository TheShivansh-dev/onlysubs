-- OnlySubscriber PostgreSQL Schema
-- Run once against your Neon database.

CREATE TABLE IF NOT EXISTS admin_panel_users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'admin',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Safe migration: add role column if table already exists without it
DO $$ BEGIN
  ALTER TABLE admin_panel_users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Safe migration: add tg_id (Telegram user/group id of the panel account)
DO $$ BEGIN
  ALTER TABLE admin_panel_users ADD COLUMN tg_id BIGINT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS registered_users (
    id                 SERIAL PRIMARY KEY,
    userid             BIGINT       NOT NULL,
    username           VARCHAR(200),
    invite_link_id     VARCHAR(50),
    invite_link_url    TEXT,
    registration_date  DATE,
    end_date           DATE,
    plan_type          VARCHAR(100),
    link_used          SMALLINT     DEFAULT 0,
    isremoved          SMALLINT     DEFAULT 0,
    removed_date       DATE
);

-- UniqueIds claimed via the external-website deep link.
-- Approach A (trust-on-first-use): a row is created the first time a
-- UniqueId is seen, bound to the Telegram user who claimed it.
CREATE TABLE IF NOT EXISTS guids (
    id             SERIAL PRIMARY KEY,
    guid           VARCHAR(64) UNIQUE NOT NULL,
    is_used        SMALLINT    DEFAULT 0,
    claimed_userid BIGINT,
    claimed_date   DATE
);

-- Safe migrations for an existing guids table
DO $$ BEGIN
  ALTER TABLE guids ADD COLUMN claimed_userid BIGINT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE guids ADD COLUMN claimed_date DATE;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
-- The single-use invite link issued for a UniqueId before anyone has joined,
-- so re-clicking re-uses the same link instead of minting extra shareable ones.
DO $$ BEGIN
  ALTER TABLE guids ADD COLUMN pending_link TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS courses (
    id          SERIAL PRIMARY KEY,
    course_name VARCHAR(200) NOT NULL,
    course_code VARCHAR(50),
    group_link  TEXT,
    group_id    BIGINT,
    is_active   SMALLINT DEFAULT 1
);

-- Safe migrations for an existing courses table
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN course_code VARCHAR(50);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN group_link TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN group_id BIGINT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
-- Soft-delete flag: deleting a course only deactivates it so existing
-- subscribers keep their course -> group mapping (no id/reflection issues).
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN is_active SMALLINT DEFAULT 1;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
-- Course-scoped admin: the panel account (login email) this course is assigned
-- to. That admin only sees/manages this course; NULL = managed by managers only.
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN assigned_admin TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
-- Optional course website/renewal link, included in expiry reminder messages.
DO $$ BEGIN
  ALTER TABLE courses ADD COLUMN website_url TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS master_groups (
    id          SERIAL PRIMARY KEY,
    group_id    BIGINT       NOT NULL,
    group_name  VARCHAR(300) DEFAULT '',
    type        VARCHAR(20)  NOT NULL,
    course_name VARCHAR(200) DEFAULT '',
    added_date  DATE,
    is_active   SMALLINT     DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pending_invites (
    id           SERIAL PRIMARY KEY,
    invite_link  TEXT        NOT NULL,
    link_name    VARCHAR(200) DEFAULT '',
    course_name  VARCHAR(200),
    months       INTEGER      DEFAULT 1,
    start_date   DATE,
    end_date     DATE,
    created_date DATE,
    is_used      SMALLINT     DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paid_users (
    id         SERIAL PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    username   VARCHAR(200) DEFAULT '',
    course     VARCHAR(100),
    start_date DATE,
    end_date   DATE,
    added_date DATE,
    is_active  SMALLINT     DEFAULT 1
);

CREATE TABLE IF NOT EXISTS invite_links (
    id           SERIAL PRIMARY KEY,
    link_id      VARCHAR(50),
    user_id      BIGINT,
    invite_link  TEXT,
    created_date DATE,
    is_revoked   SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id      SERIAL PRIMARY KEY,
    ts      TIMESTAMPTZ DEFAULT NOW(),
    actor   VARCHAR(200),
    action  VARCHAR(100),
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    label      VARCHAR(200),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default bot settings (only inserted if missing)
INSERT INTO bot_settings (key, value, label) VALUES
    ('ADMIN_GROUP_ID',       '-12342343214',       'Admin Group ID'),
    ('ADMIN_USER_ID',        '8502504224',          'SuperAdmin Telegram User ID'),
    ('BOT_CREATOR_GROUP_ID', '-1002345678901',      'Backup Group ID'),
    ('BOT_CREATOR_USER_ID',  '1234567890',          'Bot Creator User ID'),
    ('BOT_USERNAME',         '@tesingt_04bot',      'Bot Username'),
    ('SUPPORT_CONTACT',      '@helpsteno',          'Support Contact'),
    ('EXPIRY_CHECK_TIME',    '08:00',               'Daily Expiry Check Time (IST)')
ON CONFLICT (key) DO NOTHING;
