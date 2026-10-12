@{
    # RAGRAF installer scripts — analyzer configuration.
    #
    # Excluded rules and why (project convention НФТ-9 + installer UX):
    #
    # PSAvoidUsingWriteHost
    #   These scripts ARE an interactive console product: colored per-step
    #   output (OK/WARN/FAIL) is the user interface. Write-Output would pollute
    #   the pipeline and break the summary-table contract. The original scripts
    #   and the project doctrine checker use Write-Host deliberately.
    #
    # PSUseBOMForUnicodeEncodedFile
    #   Conflicts with the project's own mandatory convention: all shipped
    #   files are UTF-8 WITHOUT BOM (НФТ-9, verified by the release checks).
    #   #Requires -PSEdition Core guarantees pwsh 7+, which reads BOM-less
    #   UTF-8 correctly. Adding a BOM would fail the project's own linter.
    #
    # PSUseShouldProcessForStateChangingFunctions
    #   Stop-Install/End-InstallTranscript are installer-internal helpers with
    #   deterministic behavior (print summary, exit code). -WhatIf on an
    #   installation abort path adds noise without safety value. Destructive
    #   operations (venv removal) are explicit opt-in via -Force and guarded.
    #
    # PSReviewUnusedParameter
    #   False positive: -SummaryJson is consumed by Write-SummaryJson via
    #   script scope; the analyzer does not track script-parameter usage
    #   inside functions.
    ExcludeRules = @(
        'PSAvoidUsingWriteHost',
        'PSUseBOMForUnicodeEncodedFile',
        'PSUseShouldProcessForStateChangingFunctions',
        'PSReviewUnusedParameter'
    )
}
