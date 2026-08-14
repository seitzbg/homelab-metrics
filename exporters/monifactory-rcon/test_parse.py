from exporter import render_metrics


def test_render_metrics_parses_tps_and_entities():
    tps = open("fixtures/forge_tps.txt").read()
    ents = open("fixtures/entity_list.txt").read()
    out = render_metrics(tps, ents)
    assert 'minecraft_tps{dimension="_overall"} 20.0' in out or 'minecraft_tps{dimension="_overall"} 20.000' in out
    assert 'minecraft_entities{type="minecraft:zombie"} 30' in out
    assert 'minecraft_rcon_up 1' in out
