"""Presentation-only helpers for the calibrated headless inference contract."""

from __future__ import annotations


def format_reliable_decision(
    decision: dict | None,
    language="en",
    *,
    fit_completed: bool = False,
) -> dict:
    decision = decision or {}
    if not decision:
        if fit_completed:
            return {
                "status": "pending",
                "headline": (
                    "Автоматический подбор завершён. Расширенная проверка надёжности не запускалась."
                    if language == "ru"
                    else "Automatic fitting is complete. The extended reliability check was not run."
                ),
                "details": (
                    "Результат подбора доступен на вкладках «Лучшие параметры», "
                    "«Остатки» и «КК-проверка».\n\n"
                    "Эта вкладка предназначена для отдельной проверки устойчивости "
                    "выбора схемы. Отсутствие такой проверки не означает, что подбор "
                    "не дал результата."
                    if language == "ru"
                    else
                    "The fitting result is available in Best Parameters, Residuals, "
                    "and KK Check.\n\n"
                    "This tab is reserved for a separate circuit-selection stability "
                    "check. Its absence does not mean that fitting produced no result."
                ),
            }
        return {
            "status": "not_loaded",
            "headline": (
                "Надёжный inference не загружен."
                if language == "ru"
                else "Reliable inference has not been loaded."
            ),
            "details": (
                "Импортируйте полный JSON-результат headless reliable inference."
                if language == "ru"
                else "Import a full headless reliable-inference JSON result."
            ),
        }

    family = decision.get("recommended_family")
    topology = decision.get("recommended_topology")
    gate = decision.get("diffusion_gate") or {}
    if family == "inductive_diffusion" and gate.get("passed"):
        headline = (
            "Данные поддерживают диффузионный ECM-механизм."
            if language == "ru"
            else "Data support a diffusion-family ECM mechanism."
        )
        status = "supported"
    elif topology and decision.get("verdict") == "recommended":
        headline = (
            "Надёжная топология рекомендована."
            if language == "ru"
            else "A reliable topology was recommended."
        )
        status = "supported"
    else:
        headline = (
            "Надёжная модельная рекомендация не выдана."
            if language == "ru"
            else "No reliable model recommendation was issued."
        )
        status = "refused"

    topology_text = topology or (
        "неразличима" if language == "ru" else "indistinguishable"
    )
    lines = [
        f"{'Вердикт' if language == 'ru' else 'Verdict'}: {decision.get('verdict', '-')}",
        f"{'Статистический победитель' if language == 'ru' else 'Statistical winner'}: "
        f"{decision.get('best_statistical') or '-'}",
        f"{'Рекомендованное семейство' if language == 'ru' else 'Recommended family'}: "
        f"{family or '-'}",
        f"{'Рекомендованная топология' if language == 'ru' else 'Recommended topology'}: "
        f"{topology_text}",
        f"{'Валидность данных' if language == 'ru' else 'Data validity'}: "
        f"{decision.get('data_validity') or '-'}",
        f"{'Причина' if language == 'ru' else 'Reason'}: {decision.get('reason') or '-'}",
    ]
    if gate:
        lines.extend([
            "",
            f"{'Calibrated gate проверен' if language == 'ru' else 'Calibrated gate evaluated'}: "
            f"{bool(gate.get('evaluated'))}",
            f"{'Calibrated gate пройден' if language == 'ru' else 'Calibrated gate passed'}: "
            f"{bool(gate.get('passed'))}",
            f"positive-only: {bool(gate.get('positive_only'))}",
            f"ΔBIC: {gate.get('diffusion_family_delta_bic') if gate.get('diffusion_family_delta_bic') is not None else '-'}",
            f"{'Пороги' if language == 'ru' else 'Thresholds'}: "
            f"stability ≥ {gate.get('family_stability_threshold', '-')}, "
            f"ΔBIC ≥ {gate.get('family_delta_bic_threshold', '-')}",
        ])
    if decision.get("next_action"):
        lines.extend([
            "",
            f"{'Следующее действие' if language == 'ru' else 'Next action'}: "
            f"{decision['next_action']}",
        ])
    return {"status": status, "headline": headline, "details": "\n".join(lines)}
