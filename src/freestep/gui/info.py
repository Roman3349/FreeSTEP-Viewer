"""HTML of the Info tab: subject, exam, recording, acquisitions and images."""

from __future__ import annotations

import html

from .exam import LoadedExam, decode_images, image_size
from .i18n import tr
from .texts import acquisition_label, exam_type_label, footwear_label, format_value


def info_html(loaded: LoadedExam) -> str:
    """Build the HTML of the Info tab (subject, exam, recording and acquisition or image tables)."""
    p, e, f, post = loaded.patient, loaded.exam, loaded.filter, loaded.postura

    def table(title: str, rows: list[tuple[str, object]]) -> str:
        body = "".join(
            f"<tr><td style='color:gray;padding-right:12px'>{html.escape(k)}</td><td>{format_value(v)}</td></tr>"
            for k, v in rows
        )
        return f"<h3>{html.escape(title)}</h3><table>{body}</table>"

    gait = post is not None and post.gait
    acquisitions = [
        table(
            acquisition_label(acq, gait=gait),
            [
                (tr("Frames"), acq.frame_count),
                (tr("Duration"), f"{acq.duration_s:.2f} s"),
                (tr("Sampling rate"), f"{acq.sampling_rate_hz:.2f} Hz"),
                (tr("COP path length"), f"{acq.path_length_mm():.1f} mm"),
                (
                    tr("Sum per frame"),
                    tr("{min} … {max} (mean {mean})", min=sums.min(), max=sums.max(), mean=f"{sums.mean():.0f}"),
                ),
            ],
        )
        for acq, sums in zip(post.acquisitions if post else [], loaded.pressure_sums, strict=True)
    ]
    recording = []
    if post is not None:
        h = post.header
        recording.append(
            table(
                tr("Recording"),
                [
                    (tr("Platform"), h.platform),
                    (tr("Format version"), h.version),
                    (tr("Sensors"), f"{h.columns} × {h.rows}"),
                    (tr("Cell"), f"{h.cell_width_mm} × {h.cell_height_mm} mm"),
                    (tr("Plate"), f"{h.width_cm:g} × {h.height_cm:g} cm"),
                    *([(tr("Plates in a row"), h.plates)] if h.plates > 1 else []),
                    (tr("Max value"), h.max_value),
                    (tr("Passes") if gait else tr("Acquisitions"), len(post.acquisitions)),
                    (tr("Unknown settings"), h.settings),
                ],
            )
        )
    images = decode_images(loaded)
    if images:
        recording.append(table(tr("Images"), [(name, image_size(image, scale)) for name, image, scale in images]))
    return "".join(
        [
            table(
                tr("Subject"),
                [
                    (tr("Name"), p.name),
                    (tr("ID"), p.id),
                    (tr("Born"), p.birth_date),
                    (tr("Sex"), p.sex),
                    (tr("Height"), f"{p.height_cm:g} cm" if p.height_cm else None),
                    (tr("Weight"), f"{p.weight_kg:g} kg" if p.weight_kg else None),
                    (tr("Shoe size"), f"{p.shoe_size_eu:g} EU" if p.shoe_size_eu else None),
                    (tr("SSID"), p.ssid),
                ],
            ),
            table(
                tr("Exam"),
                [
                    (tr("ID"), e.id),
                    (tr("Date"), e.exam_date),
                    (tr("Time"), e.exam_time),
                    (tr("Type"), exam_type_label(e)),
                    (tr("Footwear"), footwear_label(e)),
                    (tr("Height at exam"), f"{e.height_cm:g} cm" if e.height_cm else None),
                    (tr("Weight at exam"), f"{e.weight_kg:g} kg" if e.weight_kg else None),
                    (tr("Filter"), tr("raw data") if f.is_raw else f"{f.filter} ({f.frequency} Hz)"),
                    (tr("Note"), e.note),
                    (tr("File"), e.file_name),
                    (tr("SSID"), e.ssid),
                ],
            ),
            *recording,
            *acquisitions,
        ]
    )
