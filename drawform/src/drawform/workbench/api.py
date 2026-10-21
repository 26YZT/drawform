"""同源受鉴权的建模接口。没有自由命令/路径或外部模型调用。"""
import json
from flask import Blueprint, abort, g, jsonify, request, send_file
from .modeling import ModelingService, Conflict
from .plate_spec import SpecError


def register(app,work,queue):
    service=ModelingService(work,queue,auto_resume=app.config.get('DRAWFORM_AUTO_RESUME',False))
    app.extensions['drawform_modeling']=service
    bp=Blueprint('workbench_v1',__name__,url_prefix='/api/workbench/v1')

    def body():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise SpecError('请求必须为JSON对象。')
        return data

    def strict_body():
        def unique(pairs):
            data={}
            for key,value in pairs:
                if key in data:raise ValueError('重复JSON字段')
                data[key]=value
            return data
        raw=request.get_data(cache=True)
        if not request.is_json or len(raw)>65536:raise SpecError('请求须为不超过64KiB的JSON对象。')
        try:data=json.loads(raw,object_pairs_hook=unique)
        except (ValueError,UnicodeError,RecursionError):raise SpecError('请求JSON无效或包含重复字段。')
        if not isinstance(data,dict):raise SpecError('请求须为JSON对象。')
        return data

    @bp.errorhandler(SpecError)
    def invalid(error):return jsonify(error=str(error),code=error.code),422
    @bp.errorhandler(Conflict)
    def conflict(error):return jsonify(error=str(error),code='PRECONDITION_FAILED'),409
    @bp.errorhandler(KeyError)
    def missing(error):return jsonify(error='没有找到该记录。',code='NOT_FOUND'),404

    @bp.get('/capabilities')
    def capabilities():return jsonify(modeling=service.capabilities())

    @bp.get('/projects')
    def projects():return jsonify(service.projects.listing(g.token_scope))

    @bp.get('/projects/<root>')
    def project(root):return jsonify(service.projects.get(root,g.token_scope))

    @bp.post('/projects/<root>/effective')
    def effective(root):
        return jsonify(service.projects.publish(root,g.token_scope,strict_body(),request.headers.get('Idempotency-Key')))

    @bp.get('/projects/<root>/publications')
    def publications(root):return jsonify(service.projects.publications(root,g.token_scope))

    @bp.get('/projects/<root>/package')
    def project_package(root):
        revision=request.args.get('revision')
        if set(request.args)-{'revision'} or len(request.args.getlist('revision'))>1:
            raise SpecError('交付下载只接受一个版本号。')
        if revision is not None:
            if not revision.isascii() or not revision.isdigit() or len(revision)>9:raise SpecError('交付版本号无效。')
            revision=int(revision)
        buffer=service.projects.package(root,g.token_scope,revision)
        response=send_file(buffer,mimetype='application/zip',as_attachment=True,download_name=f'{root}-项目交付包.zip')
        response.headers['Cache-Control']='private, no-store'
        return response

    @bp.post('/modeling/inputs')
    def inputs():
        data=body()
        if set(data)-{'text','units'}:raise SpecError('输入包含不支持的字段。')
        return jsonify(service.add_input(data.get('text'),data.get('units','mm'),g.token_scope)),201

    @bp.post('/modeling/drawing-inputs')
    def drawing_input():
        if set(request.form) or set(request.files)!={'file'} or len(request.files.getlist('file'))!=1:raise SpecError('请上传一份图纸文件，不混入默认文字或其他字段。')
        upload=request.files['file']
        data=upload.stream.read(20*1024*1024+1)
        return jsonify(service.drawings.upload(upload.filename,data,g.token_scope)),201

    @bp.post('/modeling/sessions/<sid>/answers')
    def clarify(sid):
        return jsonify(service.clarifications.answer(sid,g.token_scope,strict_body(),request.headers.get('Idempotency-Key')))

    @bp.get('/modeling/sessions/<sid>/source-preview')
    def source_preview(sid):
        response=send_file(service.drawings.preview(sid,g.token_scope),mimetype='image/png')
        response.headers['Cache-Control']='private, no-store'
        return response

    @bp.post('/modeling/sessions')
    def sessions():
        data=body()
        if set(data)-{'bundle_id','sheet'}:raise SpecError('本段只接受输入引用和纸幅，不接受代码或路径。')
        return jsonify(service.create(data.get('bundle_id'),data.get('sheet','A3'),request.headers.get('Idempotency-Key'),g.token_scope)),201

    @bp.get('/modeling/sessions/<sid>')
    def session(sid):return jsonify(service.get(sid,g.token_scope))

    @bp.post('/modeling/sessions/<sid>/revisions')
    def revise(sid):
        return jsonify(service.revisions.create(sid,g.token_scope,strict_body(),request.headers.get('Idempotency-Key'))),201

    @bp.get('/modeling/sessions/<sid>/history')
    def history(sid):return jsonify(service.revisions.history(sid,g.token_scope))

    @bp.get('/modeling/sessions/<sid>/files/<kind>')
    def artifact(sid,kind):
        path=service.file(sid,g.token_scope,kind)
        response=send_file(path,as_attachment=kind=='step',download_name=path.name)
        response.headers['Cache-Control']='private, no-store'
        return response

    @bp.post('/modeling/candidates/<sid>/adopt')
    def adopt(sid):
        data=body()
        if set(data)-{'expected_sha256'}:raise SpecError('采用请求含未知字段。')
        if not data.get('expected_sha256'):return jsonify(error='缺少模型版本依据。',code='PRECONDITION_REQUIRED'),428
        return jsonify(service.adopt(sid,g.token_scope,data['expected_sha256']))

    @bp.post('/modeling/candidates/<sid>/exports')
    def export(sid):
        data=body()
        if set(data)-{'format'}:raise SpecError('导出请求含未知字段。')
        if data.get('format','step')!='step':raise SpecError('本段仅开放STEP导出。','UNSUPPORTED_FORMAT')
        session=service.get(sid,g.token_scope);service.file(sid,g.token_scope,'step')
        return jsonify(format='STEP',representation='native_brep',step_sha256=session['step_sha256'],units='mm',download_url=session['files']['step'],human_review='not_reviewed')

    @bp.post('/documents/<sid>/drawings')
    def drawing(sid):
        data=body()
        if set(data)-{'step_sha256'}:raise SpecError('出图请求含未知字段。')
        if not data.get('step_sha256') or not request.headers.get('Idempotency-Key'):
            return jsonify(error='缺少STEP版本依据或请求编号。',code='PRECONDITION_REQUIRED'),428
        return jsonify(service.drawing(sid,g.token_scope,data['step_sha256'])),202

    app.register_blueprint(bp)
    @app.get('/workbench/')
    def page():
        from .page import PAGE
        from drawform.service.frontend import presentation_page
        return presentation_page(app, PAGE, 'workbench'),200,{'Content-Type':'text/html; charset=utf-8'}
    return service
