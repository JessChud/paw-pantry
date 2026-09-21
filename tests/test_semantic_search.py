import json

from semantic_search import SemanticRanker


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def test_semantic_ranker_batches_and_caches_catalog_vectors():
    calls = []

    def opener(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload['input'])
        vectors = []
        for index, value in enumerate(payload['input']):
            vector = [1.0, 0.0] if 'dog' in value else [0.0, 1.0]
            vectors.append({'index': index, 'embedding': vector})
        return FakeResponse({'data': vectors})

    ranker = SemanticRanker(api_key='test-secret', dimensions=2, opener=opener)
    first = ranker.rank('dog toy', {1: 'dog enrichment product', 2: 'cat food'})
    second = ranker.rank('cat meal', {1: 'dog enrichment product', 2: 'cat food'})
    assert first[1] > first[2]
    assert second[2] > second[1]
    assert len(calls[0]) == 3
    assert len(calls[1]) == 1


def test_semantic_ranker_fails_closed_without_a_key():
    ranker = SemanticRanker(api_key='')
    assert ranker.enabled is False
    assert ranker.rank('dog toy', {1: 'dog toy'}) is None
