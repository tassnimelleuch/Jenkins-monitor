from flask import session, jsonify, render_template, request, send_from_directory, url_for
from overview import overview_bp
from services.access_service import role_required
from services.dashboard_kpi_chroma_service import (
    get_dashboard_kpi_chroma_status,
    sync_dashboard_kpi_documents_to_chroma,
)
from services.dashboard_kpi_documents_service import (
    get_dashboard_kpi_document,
    get_dashboard_kpi_document_chunks,
    list_dashboard_kpi_document_chunks,
    list_dashboard_kpi_documents,
    sanitize_dashboard_kpi_summary,
    sanitize_dashboard_kpi_text,
    sync_dashboard_kpi_documents,
)
from services.jenkins_service import get_overview_kpis, request_pipeline_background_refresh
from services.export_report_service import get_pdf_report_snapshot
from services.pdf_report_storage_service import (
    get_pdf_report_path,
    get_pdf_reports_dir,
    list_pdf_reports,
    store_pdf_report,
)
from services.pipeline_storage_service import get_stored_overview_kpis
from collectors.jenkins_collector import check_connection, get_console_log
from services.azure_service import get_connection_status


def _serialize_dashboard_kpi_document(row, include_content=True):
    return {
        'id': row.id,
        'document_key': row.document_key,
        'dashboard_page': row.dashboard_page,
        'pipeline_name': row.pipeline_name,
        'pipeline_job_path': row.pipeline_job_path,
        'branch_name': row.branch_name,
        'title': row.title,
        'content': sanitize_dashboard_kpi_text(row.content) if include_content else None,
        'summary': sanitize_dashboard_kpi_summary(row.summary),
        'last_generated_at': row.last_generated_at.isoformat() if row.last_generated_at else None,
    }


def _serialize_dashboard_kpi_chunk(row, include_content=True):
    return {
        'id': row.id,
        'document_id': row.document_id,
        'document_key': row.document_key,
        'dashboard_page': row.dashboard_page,
        'pipeline_name': row.pipeline_name,
        'pipeline_job_path': row.pipeline_job_path,
        'branch_name': row.branch_name,
        'chunk_index': row.chunk_index,
        'chunk_count': row.chunk_count,
        'title': row.title,
        'content': sanitize_dashboard_kpi_text(row.content) if include_content else None,
        'summary': sanitize_dashboard_kpi_summary(row.summary),
        'last_generated_at': row.last_generated_at.isoformat() if row.last_generated_at else None,
    }


@overview_bp.route('/overview')
@role_required('admin', 'developer', 'tester')
def dashboard():
    return render_template(
        'overview.html',
        username=session.get('username'),
        role=session.get('role'),
        initial_overview_kpis=get_stored_overview_kpis(),
    )


@overview_bp.route('/pdf-reports')
@role_required('admin')
def pdf_reports_page():
    return render_template(
        'pdf_reports.html',
        username=session.get('username'),
        role=session.get('role'),
        reports=list_pdf_reports(),
        reports_dir=get_pdf_reports_dir(),
    )


@overview_bp.route('/api/pipeline/kpis')
@role_required('admin', 'developer', 'tester')
def kpis():
    if request.args.get('refresh') == '1':
        request_pipeline_background_refresh()
    return jsonify(get_overview_kpis())


@overview_bp.route('/api/status')
@role_required('admin', 'developer', 'tester')
def status():
    return jsonify({'connected': check_connection()})


@overview_bp.route('/api/log/<int:build_number>')
@role_required('admin', 'developer', 'tester')
def log_api(build_number):
    log = get_console_log(build_number)
    return jsonify({'log': log, 'build_number': build_number})


@overview_bp.route('/console/<int:build_number>')
@role_required('admin', 'developer', 'tester')
def console(build_number):
    return render_template(
        'console.html',
        build_number=build_number,
        username=session.get('username'),
        role=session.get('role')
    )

@overview_bp.route('/api/latest_build')
@role_required('admin', 'developer', 'tester')
def latest_build():
    kpis = get_overview_kpis()
    return jsonify({
        'build_number': kpis.get('last_build_number')
    })

@overview_bp.route('/api/azure/status', methods=['GET'])
@role_required('admin', 'developer', 'tester')
def azure_status():
    result = get_connection_status()
    status_code = 200 if result['connected'] else 503
    return jsonify(result), status_code


@overview_bp.route('/api/export/pdf-report', methods=['GET'])
@role_required('admin')
def export_pdf_report_api():
    try:
        payload = get_pdf_report_snapshot()
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 502
    except Exception as exc:
        return jsonify({'error': f'PDF export snapshot failed ({type(exc).__name__}): {exc}'}), 500
    return jsonify(payload), 200


@overview_bp.route('/api/export/pdf-report/store', methods=['POST'])
@role_required('admin')
def store_exported_pdf_report_api():
    file_storage = request.files.get('file')
    if file_storage is None:
        return jsonify({'error': 'No PDF file was uploaded.'}), 400

    try:
        report = store_pdf_report(
            file_storage,
            generated_at=request.form.get('generated_at'),
            preferred_file_name=request.form.get('file_name') or file_storage.filename,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': f'PDF archive failed ({type(exc).__name__}): {exc}'}), 500

    return jsonify({
        'stored': True,
        'report': {
            **report,
            'view_url': url_for('overview.view_pdf_report', file_name=report['file_name']),
            'download_url': url_for('overview.download_pdf_report', file_name=report['file_name']),
        },
    }), 201


@overview_bp.route('/pdf-reports/view/<path:file_name>')
@role_required('admin')
def view_pdf_report(file_name):
    path = get_pdf_report_path(file_name)
    if path is None:
        return jsonify({'error': 'PDF report not found.'}), 404
    return send_from_directory(str(path.parent), path.name, as_attachment=False)


@overview_bp.route('/pdf-reports/download/<path:file_name>')
@role_required('admin')
def download_pdf_report(file_name):
    path = get_pdf_report_path(file_name)
    if path is None:
        return jsonify({'error': 'PDF report not found.'}), 404
    return send_from_directory(str(path.parent), path.name, as_attachment=True)


@overview_bp.route('/api/dashboard/kpi-documents', methods=['GET'])
@role_required('admin', 'developer', 'tester')
def dashboard_kpi_documents():
    document_key = request.args.get('key')
    dashboard_page = request.args.get('page')
    limit = request.args.get('limit', default=50, type=int)

    if document_key:
        row = get_dashboard_kpi_document(document_key)
        if row is None:
            return jsonify({'error': 'No stored dashboard KPI document was found for that key.'}), 404
        return jsonify(_serialize_dashboard_kpi_document(row))

    rows = list_dashboard_kpi_documents(limit=limit, dashboard_page=dashboard_page)
    return jsonify({
        'count': len(rows),
        'documents': [
            _serialize_dashboard_kpi_document(row)
            for row in rows
        ],
    })


@overview_bp.route('/api/dashboard/kpi-documents/refresh', methods=['POST'])
@role_required('admin')
def refresh_dashboard_kpi_documents():
    try:
        result = sync_dashboard_kpi_documents()
    except Exception as exc:
        return jsonify({
            'error': f'Dashboard KPI document sync failed ({type(exc).__name__}): {exc}',
        }), 500
    return jsonify(result), 200


@overview_bp.route('/api/dashboard/kpi-documents/chunks', methods=['GET'])
@role_required('admin', 'developer', 'tester')
def dashboard_kpi_document_chunks():
    document_key = request.args.get('key')
    dashboard_page = request.args.get('page')
    limit = request.args.get('limit', default=200, type=int)

    if document_key:
        rows = get_dashboard_kpi_document_chunks(document_key)
        if not rows:
            return jsonify({'error': 'No stored dashboard KPI chunks were found for that key.'}), 404
        return jsonify({
            'count': len(rows),
            'chunks': [
                _serialize_dashboard_kpi_chunk(row)
                for row in rows
            ],
        })

    rows = list_dashboard_kpi_document_chunks(limit=limit, dashboard_page=dashboard_page)
    return jsonify({
        'count': len(rows),
        'chunks': [
            _serialize_dashboard_kpi_chunk(row)
            for row in rows
        ],
    })


@overview_bp.route('/api/dashboard/kpi-documents/chroma/status', methods=['GET'])
@role_required('admin')
def dashboard_kpi_documents_chroma_status():
    status = get_dashboard_kpi_chroma_status()
    status_code = 200 if status.get('chromadb_installed') else 503
    return jsonify(status), status_code


@overview_bp.route('/api/dashboard/kpi-documents/chroma/sync', methods=['POST'])
@role_required('admin')
def sync_dashboard_kpi_documents_chroma():
    body = request.get_json(silent=True) or {}

    try:
        result = sync_dashboard_kpi_documents_to_chroma(
            document_key=body.get('key'),
            dashboard_page=body.get('page'),
            rebuild=bool(body.get('rebuild', False)),
            auto_generate=bool(body.get('auto_generate', False)),
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({
            'error': f'Dashboard KPI Chroma sync failed ({type(exc).__name__}): {exc}',
        }), 500

    return jsonify(result), 200
