"""Where garage captures are kept, and a refusal to keep them somewhere fragile.

Raw captures are the one thing a garage session produces that cannot be
regenerated. The first sessions (2026-09-07 and 2026-09-08) wrote them to a
relative ``artifacts/garage`` inside the git worktree the scripts were run from;
that path is gitignored, the worktrees were removed when their pull requests
merged, and the captures went with them. So the default is now an absolute
directory outside every checkout, and a directory inside a linked worktree is
refused rather than used.
"""

import os

ENV_VAR = "CYRINX_GARAGE_ARTIFACTS"
DEFAULT_DIR = os.path.join("~", "dev", "cyrinx-artifacts", "garage")


def _linked_worktree_root(path):
    """The linked git worktree containing ``path``, or None.

    A linked worktree has a ``.git`` *file*; the main checkout has a ``.git``
    directory. Only the linked kind is deleted by ``git worktree remove``.
    """
    here = os.path.realpath(path)
    while True:
        dot_git = os.path.join(here, ".git")
        if os.path.isfile(dot_git):
            return here
        if os.path.isdir(dot_git):
            return None
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def session_dir(outdir=None):
    """Resolve, validate and create the directory captures are retained in.

    ``outdir`` wins, then ``$CYRINX_GARAGE_ARTIFACTS``, then
    ``~/dev/cyrinx-artifacts/garage``. Returns an absolute path.
    """
    chosen = outdir or os.environ.get(ENV_VAR) or DEFAULT_DIR
    path = os.path.abspath(os.path.expanduser(chosen))
    worktree = _linked_worktree_root(path)
    if worktree is not None:
        raise SystemExit(
            f"refusing to retain captures in {path}: it is inside the linked git worktree "
            f"{worktree}, which is deleted, ignored files included, when the worktree is "
            f"removed. Omit --outdir to use {DEFAULT_DIR}, or name a directory outside it."
        )
    os.makedirs(path, exist_ok=True)
    return path


RAW_SUBDIR = "raw-phone-pcm"


def stage_phone_pulls(harness, outdir):
    """Make the harness pull raw phone recordings into the retained directory.

    ``harness.DATA`` defaults to ``scratch/hw20k/data`` inside the checkout the
    script runs from. The tools here keep their arrays in an ``.npz`` under
    ``outdir``, but they write it after the last capture, so until then the raw
    file in the checkout is the only copy, and in a linked worktree it is deleted
    with the worktree. On 2026-11-10 a hundred such files had to be copied out by
    hand. Pulling them straight into ``outdir`` removes the step.
    """
    raw = os.path.join(outdir, RAW_SUBDIR)
    os.makedirs(raw, exist_ok=True)
    harness.DATA = raw
    return raw
