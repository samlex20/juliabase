#!/bin/sh
if [ "$1" = "build_test_main" ]
then
    ./five_chamber.py
    ./cluster_tool.py
    ./pds.py
elif [ "$1" = "synchronous" ]
then
    ./five_chamber.py
    ./cluster_tool.py
    ./pds.py
    ./solarsimulator.py
else
    ./five_chamber.py & ./cluster_tool.py
    ./pds.py & ./solarsimulator.py
fi
