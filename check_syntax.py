# check_syntax.py
import py_compile
import glob
import sys

failed = []
for p in glob.glob("**/*.py", recursive=True):
    try:
        py_compile.compile(p, doraise=True)
        print("OK:", p)
    except Exception as e:
        print("PY_COMPILE FAILED:", p, "->", e)
        failed.append(p)

print()
if failed:
    print(f"{len(failed)} file(s) failed syntax check.")
    sys.exit(2)
print("All files compiled OK.")