"""Test Pydantic models."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_composer.core.models import (
    ResearchIntent,
    GenomicRegion,
    WorkflowPlan,
    DataPreparationPlan,
    DataPrepStep,
    DataPrepAction,
    ExecutionHints,
    OutputFormat
)


class TestGenomicRegion:
    """Test GenomicRegion model."""

    def test_basic_region(self):
        region = GenomicRegion(
            name="HLA",
            chromosome="6",
            start=28477797,
            end=33448354
        )
        assert region.name == "HLA"
        assert region.chromosome == "6"
        assert region.context is None

    def test_region_with_context(self):
        region = GenomicRegion(
            name="BRCA1",
            chromosome="17",
            start=43044295,
            end=43125483,
            context="breast cancer"
        )
        assert region.context == "breast cancer"


class TestResearchIntent:
    """Test ResearchIntent model."""

    def test_basic_intent(self):
        intent = ResearchIntent(
            analysis_type="population_comparison",
            populations=["EUR", "AFR"]
        )
        assert intent.focus == "all_variants"  # Default
        assert intent.chromosomes is None

    def test_intent_with_region(self):
        region = GenomicRegion(
            name="HLA",
            chromosome="6",
            start=28477797,
            end=33448354
        )
        intent = ResearchIntent(
            analysis_type="region_analysis",
            populations=["EUR"],
            regions=[region]
        )
        assert len(intent.regions) == 1

    def test_intent_with_all_fields(self):
        intent = ResearchIntent(
            analysis_type="multi_population",
            populations=["EUR", "AFR", "EAS"],
            chromosomes=["6", "17"],
            focus="deleterious"
        )
        assert intent.focus == "deleterious"
        assert len(intent.chromosomes) == 2

    def test_invalid_analysis_type(self):
        with pytest.raises(ValueError):
            ResearchIntent(
                analysis_type="invalid_type",
                populations=["EUR"]
            )


class TestDataPreparationModels:
    """Test data preparation models."""

    def test_data_prep_step(self):
        step = DataPrepStep(
            action=DataPrepAction.DOWNLOAD,
            source="s3://bucket/file.vcf.gz",
            output_file="chr1.vcf.gz"
        )
        assert step.action == DataPrepAction.DOWNLOAD

    def test_data_preparation_plan(self):
        plan = DataPreparationPlan(
            source_type="s3",
            base_url="s3://1000genomes/release/20130502",
            steps=[
                DataPrepStep(
                    action=DataPrepAction.DOWNLOAD,
                    source="s3://bucket/file.vcf.gz",
                    output_file="chr1.vcf.gz"
                )
            ],
            estimated_transfer_mb=500.0,
            use_remote_extraction=False
        )
        assert len(plan.steps) == 1


class TestOutputFormat:
    """Test OutputFormat enum."""

    def test_hyperflow(self):
        assert OutputFormat.HYPERFLOW.value == "hyperflow"

    def test_wfcommons(self):
        assert OutputFormat.WFCOMMONS.value == "wfcommons"


class TestExecutionHints:
    """Test ExecutionHints model."""

    def test_defaults(self):
        hints = ExecutionHints()
        assert hints.prefer_remote_extraction is True
        assert hints.parallel_population_analysis is True
        assert hints.estimated_memory_per_task_gb == 2.0
        assert hints.recommended_parallelism == 10

    def test_custom_values(self):
        hints = ExecutionHints(
            prefer_remote_extraction=False,
            recommended_parallelism=50
        )
        assert hints.prefer_remote_extraction is False
        assert hints.recommended_parallelism == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
