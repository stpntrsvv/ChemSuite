from dataclasses import dataclass, field
import os
import re
import warnings

import numpy as np


@dataclass
class EisDataset:
    file_path: str
    frequencies: np.ndarray
    z: np.ndarray
    source_format: str
    columns: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ImpedanceChannel:
    name: str
    frequency_column: str
    real_column: str
    imaginary_column: str
    imaginary_mode: str


def load_eis_file(file_path: str, channel: str | None = None) -> EisDataset:
    file_path = os.fspath(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".mpr":
        return load_biologic_mpr(file_path, channel=channel)
    if ext == ".mpt":
        return load_biologic_mpt(file_path, channel=channel)
    return load_text_eis_file(file_path, channel=channel)


def load_biologic_mpr(file_path: str, channel: str | None = None) -> EisDataset:
    try:
        from galvani import BioLogic
    except ImportError as exc:
        raise ImportError("BioLogic .mpr support requires the 'galvani' package.") from exc

    mpr_file = BioLogic.MPRfile(file_path)
    return dataset_from_structured_array(
        file_path=file_path,
        data=mpr_file.data,
        source_format="biologic_mpr",
        channel=channel,
        metadata={
            "galvani_data_version": getattr(mpr_file, "version", None),
            "startdate": str(getattr(mpr_file, "startdate", "")),
            "columns": [str(name) for name in mpr_file.data.dtype.names or []],
        },
    )


def load_biologic_mpt(file_path: str, channel: str | None = None) -> EisDataset:
    try:
        from galvani import BioLogic
    except ImportError as exc:
        raise ImportError("BioLogic .mpt support requires the 'galvani' package.") from exc

    last_error = None
    for encoding in ("ascii", "utf-8", "latin1", "cp1252"):
        try:
            with open(file_path, "rb") as handle:
                data, comments = BioLogic.MPTfile(handle, encoding=encoding)
            break
        except (UnicodeDecodeError, ValueError) as exc:
            last_error = exc
    else:
        raise ValueError(f"Could not read BioLogic .mpt file with supported encodings: {last_error}")

    return dataset_from_structured_array(
        file_path=file_path,
        data=data,
        source_format="biologic_mpt",
        channel=channel,
        metadata={
            "encoding": encoding,
            "comments": [
                comment.decode("utf-8", errors="replace") if isinstance(comment, bytes) else str(comment)
                for comment in comments
            ],
            "columns": [str(name) for name in data.dtype.names or []],
        },
    )


def dataset_from_structured_array(file_path: str, data, source_format: str, metadata=None, channel: str | None = None) -> EisDataset:
    names = list(data.dtype.names or [])
    if not names:
        raise ValueError(f"{source_format} reader returned no named columns.")

    channels = detect_impedance_channels(names)
    selected_channel = choose_impedance_channel(channels, channel)

    freq_col = selected_channel.frequency_column
    re_col = selected_channel.real_column
    im_col = selected_channel.imaginary_column
    im_mode = selected_channel.imaginary_mode

    frequencies = np.asarray(data[freq_col], dtype=float)
    re_values = np.asarray(data[re_col], dtype=float)
    im_values = np.asarray(data[im_col], dtype=float)
    z_imag = imaginary_to_z_imag(im_values, im_mode)

    return clean_dataset(
        EisDataset(
            file_path=file_path,
            frequencies=frequencies,
            z=re_values + 1j * z_imag,
            source_format=source_format,
            columns=names,
            metadata={
                **(metadata or {}),
                "frequency_column": freq_col,
                "real_column": re_col,
                "imaginary_column": im_col,
                "imaginary_mode": im_mode,
                "selected_channel": selected_channel.name,
                "available_channels": [detected.name for detected in channels],
            },
        )
    )


def load_text_eis_file(file_path: str, channel: str | None = None) -> EisDataset:
    if "readme" in os.path.basename(file_path).lower():
        raise ValueError(f"{file_path} is a README/metadata file, not an EIS spectrum.")
    table = try_load_named_text_table(file_path, channel=channel)
    if table is not None:
        return table
    if channel is not None and normalize_column_name(channel) != "z":
        raise ValueError(f"Requested impedance channel {channel!r} is not available")
    table = try_load_embedded_eis_table(file_path)
    if table is not None:
        return table
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
        header_probe = "".join(handle.readlines()[:5]).lower()
    if "cycle number" in header_probe and "frequency" not in header_probe:
        raise ValueError(
            f"{file_path} contains cycle-indexed impedance values but no frequency column; "
            "frequency reconstruction would be speculative."
        )

    data_lines = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        for line in file:
            matches = re.findall(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?|\b[-+]?\d+\b", line)
            if len(matches) >= 3:
                data_lines.append([float(value) for value in matches[:3]])

    if not data_lines:
        raise ValueError(f"Could not find three numeric columns in {file_path}")

    data = np.array(data_lines, dtype=float)
    candidate_frequency = data[:, 0]
    finite_frequency = candidate_frequency[np.isfinite(candidate_frequency)]
    differences = np.diff(finite_frequency)
    monotonic_fraction = max(
        float(np.mean(differences > 0)) if len(differences) else 0.0,
        float(np.mean(differences < 0)) if len(differences) else 0.0,
    )
    if len(np.unique(finite_frequency[finite_frequency > 0])) < 8 or monotonic_fraction < 0.8:
        raise ValueError(
            f"Numeric text in {file_path} does not contain a credible monotonic frequency column."
        )
    z_imag = imaginary_to_z_imag(data[:, 2], "auto")
    return clean_dataset(
        EisDataset(
            file_path=file_path,
            frequencies=data[:, 0],
            z=data[:, 1] + 1j * z_imag,
            source_format="generic_numeric_text",
            columns=["frequency", "re", "im"],
            metadata={
                "imaginary_mode": "auto",
                "selected_channel": "Z",
                "available_channels": ["Z"],
            },
        )
    )


def try_load_embedded_eis_table(file_path: str) -> EisDataset | None:
    """Read vendor exports whose actual EIS table follows a metadata preamble."""
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
        lines = handle.readlines()
    for line_index, line in enumerate(lines):
        for delimiter in ("\t", ",", ";"):
            parts = [part.strip() for part in line.strip().split(delimiter)]
            normalized = [normalize_column_name(part) for part in parts]
            frequency_index = next((i for i, name in enumerate(normalized) if
                                    name in {normalize_column_name(alias) for alias in FREQUENCY_ALIASES}), None)
            real_index = next((i for i, name in enumerate(normalized) if
                               name in {normalize_column_name(alias) for alias in REAL_ALIASES}), None)
            negative_imag_index = next((i for i, name in enumerate(normalized) if
                                        name in {normalize_column_name(alias) for alias in NEG_IMAG_ALIASES}), None)
            raw_imag_index = next((i for i, name in enumerate(normalized) if
                                   name in {normalize_column_name(alias) for alias in RAW_IMAG_ALIASES}), None)
            imag_index = negative_imag_index if negative_imag_index is not None else raw_imag_index
            if frequency_index is None or real_index is None or imag_index is None:
                continue
            rows = []
            for data_line in lines[line_index + 1:]:
                fields = [field.strip() for field in data_line.strip().split(delimiter)]
                if len(fields) <= max(frequency_index, real_index, imag_index):
                    if rows:
                        break
                    continue
                try:
                    rows.append((float(fields[frequency_index]), float(fields[real_index]), float(fields[imag_index])))
                except ValueError:
                    if rows:
                        break
            if not rows:
                continue
            data = np.asarray(rows, dtype=float)
            mode = "negative_imaginary" if negative_imag_index is not None else "raw_imaginary"
            return clean_dataset(EisDataset(
                file_path=file_path,
                frequencies=data[:, 0],
                z=data[:, 1] + 1j * imaginary_to_z_imag(data[:, 2], mode),
                source_format="embedded_named_text_table",
                columns=parts,
                metadata={
                    "embedded_header_line": line_index + 1,
                    "delimiter": delimiter,
                    "frequency_column": parts[frequency_index],
                    "real_column": parts[real_index],
                    "imaginary_column": parts[imag_index],
                    "imaginary_mode": mode,
                    "selected_channel": "Z",
                    "available_channels": ["Z"],
                },
            ))
    return None


def try_load_named_text_table(file_path: str, channel: str | None = None):
    for delimiter in ("\t", ",", ";", None):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                kwargs = {
                    "delimiter": delimiter,
                    "dtype": float,
                    "encoding": "utf-8",
                    "invalid_raise": False,
                    "autostrip": True,
                }
                explicit_names = leading_unnamed_column_names(file_path, delimiter)
                if explicit_names:
                    data = np.genfromtxt(file_path, names=explicit_names, skip_header=1, **kwargs)
                else:
                    data = np.genfromtxt(file_path, names=True, **kwargs)
        except Exception:
            continue

        names = list(data.dtype.names or [])
        if len(names) < 3:
            continue

        if not detect_impedance_channels(names):
            continue
        if channel is not None:
            choose_impedance_channel(detect_impedance_channels(names), channel)
        try:
            return dataset_from_structured_array(
                file_path=file_path,
                data=data,
                source_format="named_text_table",
                channel=channel,
                metadata={"delimiter": delimiter, "columns": names},
            )
        except Exception:
            continue
    return None


def leading_unnamed_column_names(file_path: str, delimiter: str | None) -> list[str] | None:
    """Preserve empty index columns and minus signs stripped by genfromtxt."""
    if delimiter not in {",", ";", "\t"}:
        return None
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
            header = handle.readline().strip("\r\n")
    except OSError:
        return None
    parts = [part.strip() for part in header.split(delimiter)]
    if not parts or (parts[0] and not any(part.startswith("-") for part in parts)):
        return None
    if not parts[0]:
        parts[0] = "index"
    parts = [f"minus{part[1:]}" if part.startswith("-") else part for part in parts]
    return [part or f"unnamed_{index}" for index, part in enumerate(parts)]


def detect_impedance_channels(names: list[str]) -> list[ImpedanceChannel]:
    channels = []
    try:
        freq_col = choose_column(names, FREQUENCY_ALIASES, kind="frequency")
    except ValueError:
        return []

    preferred_specs = [
        ("Z", REAL_ALIASES, NEG_IMAG_ALIASES, RAW_IMAG_ALIASES),
        ("Zce", aliases_for_real("zce"), aliases_for_negative_imag("zce"), aliases_for_raw_imag("zce")),
        ("Zstack", aliases_for_real("zstack"), aliases_for_negative_imag("zstack"), aliases_for_raw_imag("zstack")),
        ("Zwe-ce", aliases_for_real("zwe-ce"), aliases_for_negative_imag("zwe-ce"), aliases_for_raw_imag("zwe-ce")),
        ("Z1", aliases_for_real("z1"), aliases_for_negative_imag("z1"), aliases_for_raw_imag("z1")),
        ("Z2", aliases_for_real("z2"), aliases_for_negative_imag("z2"), aliases_for_raw_imag("z2")),
    ]

    for name, real_aliases, negative_imag_aliases, raw_imag_aliases in preferred_specs:
        try:
            real_col = choose_column(names, real_aliases, kind=f"{name} real impedance")
            imag_col, imag_mode = choose_imaginary_column_from_aliases(names, negative_imag_aliases, raw_imag_aliases)
        except ValueError:
            continue
        channels.append(ImpedanceChannel(name, freq_col, real_col, imag_col, imag_mode))

    return channels


def choose_impedance_channel(channels: list[ImpedanceChannel], requested: str | None) -> ImpedanceChannel:
    if not channels:
        raise ValueError("Could not detect any EIS impedance channel with frequency, Re(Z), and Im(Z) columns.")

    if requested:
        normalized_requested = normalize_column_name(requested)
        for channel in channels:
            if normalize_column_name(channel.name) == normalized_requested:
                return channel
        raise ValueError(
            f"Requested impedance channel '{requested}' is not available. "
            f"Available channels: {', '.join(channel.name for channel in channels)}"
        )

    for preferred in ("Z", "Z1", "Zwe-ce", "Zce", "Zstack", "Z2"):
        for channel in channels:
            if channel.name == preferred:
                return channel
    return channels[0]


def aliases_for_real(channel_key: str) -> tuple[str, ...]:
    return (f"re({channel_key})/ohm", f"re{channel_key}/ohm", f"re{channel_key}ohm", f"re{channel_key}")


def aliases_for_negative_imag(channel_key: str) -> tuple[str, ...]:
    return (
        f"-im({channel_key})/ohm",
        f"-im{channel_key}/ohm",
        f"-im{channel_key}ohm",
        f"minusim{channel_key}ohm",
    )


def aliases_for_raw_imag(channel_key: str) -> tuple[str, ...]:
    return (f"im({channel_key})/ohm", f"im{channel_key}/ohm", f"im{channel_key}ohm", f"im{channel_key}")


FREQUENCY_ALIASES = (
    "freq/hz",
    "frqhz",
    "frq/hz",
    "frequencyhz",
    "frequency/hz",
    "f/hz",
    "freq",
    "frequency",
    "f",
    "frequency (hz)",
)

REAL_ALIASES = (
    "re(z)/ohm",
    "rez/ohm",
    "rezohm",
    "reohm",
    "zre",
    "realz",
    "real",
    "real impedance",
    "realohm",
    "zreal",
    "re",
    "impedance' (Ω)",
    "impedance' (ohm)",
)

NEG_IMAG_ALIASES = (
    "-im(z)/ohm",
    "-imz/ohm",
    "-imzohm",
    "minusimzohm",
    "negimzohm",
    "-im",
    "-zimag",
    "minuszimag",
)

RAW_IMAG_ALIASES = (
    "im(z)/ohm",
    "imz/ohm",
    "imzohm",
    "imohm",
    "zim",
    "imagz",
    "imaginary",
    "imaginary impedance",
    "imagohm",
    "zimag",
    "im",
    "impedance'' (Ω)",
    "impedance'' (ohm)",
)


def choose_column(names, aliases, kind: str) -> str:
    normalized = {normalize_column_name(name): name for name in names}
    for alias in aliases:
        normalized_alias = normalize_column_name(alias)
        if normalized_alias in normalized:
            return normalized[normalized_alias]

    alias_parts = [normalize_column_name(alias) for alias in aliases]
    for normalized_name, original_name in normalized.items():
        if any(len(alias) >= 4 and alias in normalized_name for alias in alias_parts):
            return original_name

    raise ValueError(f"Could not identify {kind} column. Available columns: {', '.join(names)}")


def choose_imaginary_column(names) -> tuple[str, str]:
    return choose_imaginary_column_from_aliases(names, NEG_IMAG_ALIASES, RAW_IMAG_ALIASES)


def choose_imaginary_column_from_aliases(names, negative_aliases, raw_aliases) -> tuple[str, str]:
    normalized = {normalize_column_name(name): name for name in names}
    for alias in negative_aliases:
        if alias in normalized:
            return normalized[alias], "negative_imaginary"
    for alias in raw_aliases:
        if alias in normalized:
            return normalized[alias], "raw_imaginary"

    negative_parts = [normalize_column_name(alias) for alias in negative_aliases]
    raw_parts = [normalize_column_name(alias) for alias in raw_aliases]
    for normalized_name, original_name in normalized.items():
        if any(alias in normalized_name for alias in negative_parts):
            return original_name, "negative_imaginary"
        if any(alias in normalized_name for alias in raw_parts):
            return original_name, "raw_imaginary"

    for normalized_name, original_name in normalized.items():
        if "im" in normalized_name:
            return original_name, "auto"

    raise ValueError(f"Could not identify imaginary impedance column. Available columns: {', '.join(names)}")


def normalize_column_name(name: str) -> str:
    normalized = str(name).strip().lower()
    normalized = normalized.replace("ω", "ohm")
    normalized = normalized.replace("ω", "ohm")
    normalized = normalized.replace("Ω", "ohm")
    normalized = normalized.replace("Ω", "ohm")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("_", "")
    normalized = normalized.replace("(", "")
    normalized = normalized.replace(")", "")
    normalized = normalized.replace("[", "")
    normalized = normalized.replace("]", "")
    return normalized


def imaginary_to_z_imag(values, mode: str):
    values = np.asarray(values, dtype=float)
    if mode == "negative_imaginary":
        return -values
    if mode == "raw_imaginary":
        return values

    mean_im = float(np.nanmean(values))
    return values if mean_im < 0 else -values


def clean_dataset(dataset: EisDataset) -> EisDataset:
    raw_point_count = len(dataset.frequencies)
    finite_mask = np.isfinite(dataset.frequencies) & np.isfinite(dataset.z.real) & np.isfinite(dataset.z.imag)
    nonpositive_count = int(np.sum(finite_mask & (np.asarray(dataset.frequencies) <= 0)))
    mask = finite_mask & (np.asarray(dataset.frequencies) > 0)
    frequencies = np.asarray(dataset.frequencies[mask], dtype=float)
    z_values = np.asarray(dataset.z[mask], dtype=complex)

    if len(frequencies) == 0:
        raise ValueError(f"No finite positive-frequency EIS rows found in {dataset.file_path}")

    unique_frequencies, inverse, counts = np.unique(frequencies, return_inverse=True, return_counts=True)
    duplicate_groups = int(np.sum(counts > 1))
    duplicate_points = int(np.sum(counts - 1))
    if duplicate_points:
        aggregated = np.empty(len(unique_frequencies), dtype=complex)
        for index in range(len(unique_frequencies)):
            group = z_values[inverse == index]
            aggregated[index] = np.median(group.real) + 1j * np.median(group.imag)
        frequencies = unique_frequencies
        z_values = aggregated

    order = np.argsort(frequencies)[::-1]
    dataset.frequencies = frequencies[order]
    dataset.z = z_values[order]
    dataset.metadata.update(
        {
            "raw_point_count": raw_point_count,
            "fit_point_count": len(dataset.frequencies),
            "dropped_nonfinite_points": int(raw_point_count - np.sum(finite_mask)),
            "dropped_nonpositive_frequency_points": nonpositive_count,
            "duplicate_frequency_groups": duplicate_groups,
            "aggregated_duplicate_points": duplicate_points,
            "duplicate_aggregation": "median_complex_by_exact_frequency" if duplicate_points else "none",
        }
    )
    return dataset
