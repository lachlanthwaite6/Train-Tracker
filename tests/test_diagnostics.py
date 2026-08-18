from train_tracker.outputs.diagnostics import DiagnosticPattern, render_diagnostic


def test_all_diagnostics_are_exact_frames() -> None:
    for pattern in DiagnosticPattern:
        frame = render_diagnostic(pattern, frame_number=42)
        assert frame.size == (128, 64)
        assert frame.mode == "RGB"
