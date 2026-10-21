#!/usr/bin/env python3


import os
import sys

prompt = " ".join(sys.argv[1:]).lower()
if "username" in prompt:
    print("x-access-token")
elif "password" in prompt:
    print(os.environ.get("LMSTUDIO_GIT_GITHUB_TOKEN", ""))
else:
    print("")
