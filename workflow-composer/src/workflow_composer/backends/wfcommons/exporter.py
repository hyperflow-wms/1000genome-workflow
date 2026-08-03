"""
WfCommons exporter.

Converts HyperFlow JSON (from ``backends.hyperflow.generator``) to the
WfCommons workflow-trace format. See https://wfcommons.org/
"""
from __future__ import annotations


def to_wfcommons(hf: dict) -> dict:
    """Convert HyperFlow JSON to WfCommons format.

    WfCommons is a community standard for workflow execution traces.
    See: https://wfcommons.org/
    """
    # Build signal ID → name mapping
    signal_names = {i: s["name"] for i, s in enumerate(hf["signals"])}

    # Build output → task mapping for dependency resolution
    output_to_task = {}
    for i, proc in enumerate(hf["processes"]):
        for out_id in proc["outs"]:
            output_to_task[out_id] = i

    tasks = []
    for i, proc in enumerate(hf["processes"]):
        # Map input signal IDs to file names
        input_files = [{"name": signal_names[sid]} for sid in proc["ins"]]
        output_files = [{"name": signal_names[sid]} for sid in proc["outs"]]

        # Determine parent tasks (who produces our inputs)
        parents = []
        for in_id in proc["ins"]:
            if in_id in output_to_task:
                parent_idx = output_to_task[in_id]
                parent_name = f"task_{parent_idx}"
                if parent_name not in parents:
                    parents.append(parent_name)

        tasks.append({
            "name": f"task_{i}",
            "id": f"task_{i}",
            "type": proc["name"],
            "command": {
                "program": proc["config"]["executor"]["executable"],
                "arguments": proc["config"]["executor"]["args"]
            },
            "parents": parents,
            "inputFiles": input_files,
            "outputFiles": output_files,
            "runtimeInSeconds": 0,  # Unknown at planning time
            "memoryInBytes": 0
        })

    return {
        "name": hf.get("name", "workflow"),
        "schemaVersion": "1.3",
        "createdAt": "",  # To be filled by caller
        "workflow": {
            "specification": "1000genome",
            "tasks": tasks
        }
    }
