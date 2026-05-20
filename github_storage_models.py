from datetime import datetime, timezone

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class GitHubCommit(db.Model):
    __tablename__ = 'github_commits'
    __table_args__ = (
        db.UniqueConstraint(
            'owner',
            'repo',
            'sha',
            name='uq_github_commit_repo_sha',
        ),
        db.Index(
            'ix_github_commit_repo_branch_committed_at',
            'owner',
            'repo',
            'branch_name',
            'committed_at',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner = db.Column(db.String(255), nullable=False, index=True)
    repo = db.Column(db.String(255), nullable=False, index=True)
    sha = db.Column(db.String(64), nullable=False, index=True)
    branch_name = db.Column(db.String(255), nullable=False, default='main', index=True)
    message = db.Column(db.Text, nullable=True)
    author_name = db.Column(db.String(255), nullable=True)
    author_login = db.Column(db.String(255), nullable=True)
    author_avatar = db.Column(db.String(1024), nullable=True)
    author_profile_url = db.Column(db.String(1024), nullable=True)
    committer_name = db.Column(db.String(255), nullable=True)
    committer_login = db.Column(db.String(255), nullable=True)
    committer_avatar = db.Column(db.String(1024), nullable=True)
    committer_profile_url = db.Column(db.String(1024), nullable=True)
    committed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    html_url = db.Column(db.String(1024), nullable=True)
    additions = db.Column(db.Integer, nullable=False, default=0)
    deletions = db.Column(db.Integer, nullable=False, default=0)
    total_changes = db.Column(db.Integer, nullable=False, default=0)
    changed_files_count = db.Column(db.Integer, nullable=False, default=0)
    files_detail_available = db.Column(db.Boolean, nullable=False, default=False)
    source_system = db.Column(db.String(32), nullable=False, default='github_api')
    last_synced_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    files = db.relationship(
        'GitHubCommitFile',
        back_populates='commit',
        cascade='all, delete-orphan',
        lazy=True,
        order_by=lambda: (
            GitHubCommitFile.filename.asc(),
            GitHubCommitFile.id.asc(),
        ),
    )


class GitHubCommitFile(db.Model):
    __tablename__ = 'github_commit_files'
    __table_args__ = (
        db.UniqueConstraint(
            'commit_id',
            'filename',
            name='uq_github_commit_file_name',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    commit_id = db.Column(
        db.Integer,
        db.ForeignKey('github_commits.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    filename = db.Column(db.String(1024), nullable=False)
    previous_filename = db.Column(db.String(1024), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='modified')
    additions = db.Column(db.Integer, nullable=False, default=0)
    deletions = db.Column(db.Integer, nullable=False, default=0)
    changes = db.Column(db.Integer, nullable=False, default=0)
    last_synced_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    commit = db.relationship(
        'GitHubCommit',
        back_populates='files',
    )


class GitHubRepoSyncState(db.Model):
    __tablename__ = 'github_repo_sync_states'
    __table_args__ = (
        db.UniqueConstraint(
            'owner',
            'repo',
            'branch_name',
            'dataset',
            name='uq_github_repo_sync_state_scope',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner = db.Column(db.String(255), nullable=False, index=True)
    repo = db.Column(db.String(255), nullable=False, index=True)
    branch_name = db.Column(db.String(255), nullable=False, default='main', index=True)
    dataset = db.Column(db.String(32), nullable=False, default='commit_history', index=True)
    source_system = db.Column(db.String(32), nullable=False, default='github_api')
    last_attempted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )


def ensure_github_storage_schema():
    GitHubCommit.__table__.create(bind=db.engine, checkfirst=True)
    GitHubCommitFile.__table__.create(bind=db.engine, checkfirst=True)
    GitHubRepoSyncState.__table__.create(bind=db.engine, checkfirst=True)
    return True
