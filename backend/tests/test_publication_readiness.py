from app.brand.publication import dependency, reconcile


def test_attachment_resolution_does_not_grant_rights():
    draft = {'body': 'Read more', 'image': {'image_id': 'picture'},
             'validation': {'status': 'passed'}, 'dependency_records': [
                 dependency('image_attachment', 'Add image'), dependency('image_review', 'Check rights')]}
    result = reconcile(draft, {'id': 'picture'})
    assert result['dependency_records'][0]['status'] == 'resolved'
    assert result['dependency_records'][1]['status'] == 'unresolved'
    assert result['production_dependencies'] == ['Confirm the selected image rights and placement against its current library record.']
    assert result['status'] == 'needs_review'
    assert draft['dependency_records'][0]['status'] == 'unresolved'
    removed = reconcile({**result, 'image': None})
    assert removed['dependency_records'][0]['status'] == 'unresolved'


def test_url_must_be_in_copy_and_author_must_be_supplied():
    draft = {'body': 'Read more', 'validation': {'status': 'passed'},
             'publication_details': {'destination_url': 'https://example.test/article'},
             'dependency_records': [dependency('destination_url', 'Supply article URL'),
                                    dependency('author', 'Supply author')]}
    result = reconcile(draft)
    assert result['status'] == 'needs_review'
    result['body'] += ' https://example.test/article'
    result['byline'] = 'Confirmed Author, Engineer'
    result = reconcile(result)
    assert result['status'] == 'completed'
    assert result['production_dependencies'] == []


def test_legacy_unknown_dependencies_are_never_assumed_resolved():
    draft = {'body': 'Copy', 'validation': {'status': 'passed'},
             'production_dependencies': ['Check permission before publishing']}
    assert reconcile(draft)['status'] == 'needs_review'


def test_duplicate_dependency_warnings_are_grouped_by_requirement():
    draft = {'body': 'Copy', 'validation': {'status': 'passed'}, 'dependency_records': [
        dependency('destination_url', 'Supply campaign URL', 's1'),
        dependency('destination_url', 'Link the CTA to the campaign URL', 's2')]}
    result = reconcile(draft)
    assert len(result['dependency_records']) == 1
    assert result['dependency_records'][0]['additional_source_refs'] == ['s2']
