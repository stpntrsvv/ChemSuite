"""Presentation contracts; no scientific imports or calculations."""
from chem_suite.locale import text


def decision_text(decision, language):
    labels = [('verdict', 'Verdict'), ('best_statistical', 'Statistical winner'),
              ('recommended_family', 'Recommended family'), ('recommended_topology', 'Recommended topology'),
              ('data_validity', 'Data validity'), ('fit_status', 'Model status'), ('reason', 'Reason'),
              ('next_action', 'Next action')]
    lines = [text(title, language) + ': ' + text(decision.get(key) or '—', language) for key, title in labels]
    gate = decision.get('diffusion_gate')
    if gate:
        lines += ['', text('Positive diffusion gate', language),
                  text('Evaluated', language) + ': ' + text(gate.get('evaluated'), language),
                  text('Passed', language) + ': ' + text(gate.get('passed'), language),
                  'ΔBIC: ' + str(gate['diffusion_family_delta_bic'] if gate.get('diffusion_family_delta_bic') is not None else '—'),
                  text('Thresholds', language) + ': ' + str(gate.get('family_stability_threshold')) + ' / ΔBIC ≥ ' + str(gate.get('family_delta_bic_threshold')),
                  text('This gate supports presence only; it does not prove absence of diffusion or select W/Wo/Ws.', language)]
    return '\n'.join(lines)


def statistics_tables(statistics):
    tables = {'Parameter intervals': statistics['bootstrap']['parameters'], 'Bootstrap summary': [
        {key: value for key, value in statistics['bootstrap'].items() if key != 'parameters'}]}
    if statistics.get('profile'):
        profile = statistics['profile']
        tables['Profile summary'] = [{key: value for key, value in profile.items() if key != 'points'}]
        tables['Profile points'] = profile['points']
    if statistics.get('window_stability'):
        tables['Window stability'] = [{'name': name, **row} for name, row in statistics['window_stability']['parameters'].items()]
        tables['Frequency windows'] = statistics['window_stability']['variants']
    tables['Characteristic frequency support'] = [{'name': name, **(row or {'supported': None})}
        for name, row in statistics['characteristic_support'].items()]
    return tables
