"""Test format export."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_composer.core.models import OutputFormat
from workflow_composer.core.generator import generate_workflow
from workflow_composer.core.export import convert_workflow, to_wfcommons

WORKFLOW_GENERATOR_PATH = Path(__file__).parent.parent.parent / "workflow-generator"


class TestWfCommonsExport:
    """Test WfCommons format conversion."""

    def test_wfcommons_export(self):
        """Test conversion to WfCommons format."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        # Generate a small workflow
        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        wfc = to_wfcommons(wf)

        assert wfc["schemaVersion"] == "1.3"
        assert "workflow" in wfc
        assert "tasks" in wfc["workflow"]
        assert len(wfc["workflow"]["tasks"]) == len(wf["processes"])

    def test_wfcommons_preserves_dependencies(self):
        """Test that WfCommons export preserves task dependencies."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        wfc = to_wfcommons(wf)

        # Find individuals_merge tasks - they should have parents (individuals tasks)
        merge_tasks = [t for t in wfc["workflow"]["tasks"] if "individuals_merge" in t["type"]]

        for task in merge_tasks:
            assert len(task["parents"]) > 0, "individuals_merge should have parent tasks"

    def test_wfcommons_task_structure(self):
        """Test WfCommons task structure."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        wfc = to_wfcommons(wf)

        for task in wfc["workflow"]["tasks"]:
            assert "name" in task
            assert "id" in task
            assert "type" in task
            assert "command" in task
            assert "program" in task["command"]
            assert "arguments" in task["command"]
            assert "parents" in task
            assert "inputFiles" in task
            assert "outputFiles" in task


class TestConvertWorkflow:
    """Test convert_workflow dispatcher."""

    def test_hyperflow_passthrough(self):
        """HyperFlow format should pass through unchanged."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        result = convert_workflow(wf, OutputFormat.HYPERFLOW)
        assert result is wf  # Should be same object

    def test_wfcommons_conversion(self):
        """WfCommons conversion should work."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        result = convert_workflow(wf, OutputFormat.WFCOMMONS)
        assert "workflow" in result
        assert "tasks" in result["workflow"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
