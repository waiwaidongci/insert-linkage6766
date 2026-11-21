from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def change_order(item, conflict=False):
    return {'id': item.id, 'factory': item.factory, 'order_no': item.order_no,
            'action': item.action, 'spec_id': item.spec_id, 'name': item.name,
            'fit_traits': item.fit_traits, 'state': item.state, 'ruled': item.ruled,
            'conflict': conflict, 'author': item.author, 'created_at': iso(item.created_at)}


def notice(item, conflict=False, cycle_id=None):
    return {'id': item.id, 'factory': item.factory, 'delivery_no': item.delivery_no,
            'covers_through': item.covers_through, 'state': item.state, 'ruled': item.ruled,
            'conflict': conflict, 'cycle_id': cycle_id, 'author': item.author,
            'created_at': iso(item.created_at)}


def cycle(item, notices, issues, current_dataset_id):
    return {'id': item.id, 'state': item.state, 'detail': item.detail, 'attempt': item.attempt,
            'dataset_id': item.dataset_id, 'base_dataset_id': item.base_dataset_id,
            'is_current': bool(item.dataset_id) and item.dataset_id == current_dataset_id,
            'issues': issues,
            'covers': {factory: {'delivery_no': notice.delivery_no,
                                 'covers_through': notice.covers_through}
                       for factory, notice in notices.items()},
            'created_at': iso(item.created_at), 'published_at': iso(item.published_at)}
