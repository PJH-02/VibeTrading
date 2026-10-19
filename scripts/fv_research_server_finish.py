"""Complete sealed GRVT/Arcus archive derivations in an isolated container."""
import subprocess,sys
for venue in ('grvt','arcus'):
    subprocess.run([sys.executable,'fv_research_reduce.py',venue],check=True)
    subprocess.run([sys.executable,'shard_closed_fv_research.py',venue],check=True)
