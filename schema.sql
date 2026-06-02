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

CREATE TABLE IF NOT EXISTS guids (
    id       SERIAL PRIMARY KEY,
    guid     VARCHAR(20) UNIQUE NOT NULL,
    is_used  SMALLINT    DEFAULT 0
);

CREATE TABLE IF NOT EXISTS courses (
    id          SERIAL PRIMARY KEY,
    course_name VARCHAR(200) NOT NULL
);

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
