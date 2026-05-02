CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    source_name TEXT NOT NULL,
    raw_content TEXT,
    full_text TEXT,
    summary TEXT,
    word_count INTEGER NOT NULL DEFAULT 0,
    extraction_ok INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'raw',
    relevance_score REAL,
    urgency TEXT,
    richness TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    default_score REAL NOT NULL DEFAULT 0.0,
    hot_score REAL NOT NULL DEFAULT 0.0,
    engagement_score REAL NOT NULL DEFAULT 0.0,
    vote_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT 'fetched',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    parent_comment_id INTEGER REFERENCES comments(id),
    author_type TEXT NOT NULL,
    author_id TEXT,
    author_name TEXT NOT NULL,
    body TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    vote_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    edited_at TEXT
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER REFERENCES posts(id),
    comment_id INTEGER REFERENCES comments(id),
    voter_type TEXT NOT NULL,
    voter_id TEXT,
    direction INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (post_id IS NOT NULL OR comment_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS archetypes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    bio TEXT NOT NULL,
    role TEXT NOT NULL,
    tone TEXT,
    sentence_style TEXT,
    vocabulary_level TEXT,
    quirks TEXT,
    example_comment TEXT,
    favors TEXT NOT NULL DEFAULT '[]',
    dislikes TEXT NOT NULL DEFAULT '[]',
    indifferent TEXT NOT NULL DEFAULT '[]',
    vote_probability REAL NOT NULL DEFAULT 0.7,
    comment_threshold REAL NOT NULL DEFAULT 0.5,
    reply_probability REAL NOT NULL DEFAULT 0.6,
    verbosity TEXT NOT NULL DEFAULT 'medium',
    contrarian_factor REAL NOT NULL DEFAULT 0.1,
    temperature REAL NOT NULL DEFAULT 0.7,
    max_instances INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    archetype_id INTEGER NOT NULL REFERENCES archetypes(id),
    archetype_version INTEGER NOT NULL,
    name TEXT NOT NULL,
    drift_vector TEXT NOT NULL DEFAULT '{}',
    memory TEXT NOT NULL DEFAULT '{}',
    session_count INTEGER NOT NULL DEFAULT 0,
    last_session TEXT,
    mood TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL REFERENCES instances(id),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    phase TEXT,
    posts_triaged INTEGER NOT NULL DEFAULT 0,
    posts_engaged INTEGER NOT NULL DEFAULT 0,
    comments_made INTEGER NOT NULL DEFAULT 0,
    votes_cast INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    avatar_name TEXT NOT NULL,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    post_title TEXT NOT NULL,
    comment_id INTEGER NOT NULL REFERENCES comments(id),
    body TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_hot_score ON posts(hot_score DESC);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX IF NOT EXISTS idx_votes_post_id ON votes(post_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_post_voter ON votes(post_id, voter_type, voter_id) WHERE post_id IS NOT NULL AND voter_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_comment_voter ON votes(comment_id, voter_type, voter_id) WHERE comment_id IS NOT NULL AND voter_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_instances_archetype ON instances(archetype_id);

CREATE TABLE IF NOT EXISTS editorials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL REFERENCES instances(id),
    body TEXT NOT NULL,
    mood TEXT,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_editorials_instance ON editorials(instance_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id INTEGER NOT NULL REFERENCES comments(id),
    instance_id TEXT NOT NULL REFERENCES instances(id),
    post_id INTEGER NOT NULL REFERENCES posts(id),
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mentions_instance ON mentions(instance_id, resolved);
