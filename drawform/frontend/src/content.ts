export type SystemId = 'battery' | 'drive' | 'optics'
export const systems = {
  battery: {
    index: '02', label: '文字建模', category: '文字 → 模型 → 工程图', title: '把描述，\n变成模型。',
    short: '了解文字建模流程', href: '/workbench/?mode=text', cta: '打开文字建模工作区',
    description: '描述简单零件的形状与尺寸。缺少关键信息时先澄清，再生成可查看的实体模型。',
    benefit: '在工作区查看真实模型，确认采用后导出 STEP，再生成二维工程图。',
    image: '/media/battery-blue.png', anchor: { x: 64, y: 72 },
    points: [
      { label: '描述零件', text: '输入板件、圆盘、垫圈或二段阶梯轴等受支持的简单零件描述。', x: 31, y: 42 },
      { label: '补齐尺寸', text: '逐项核对缺少的尺寸、单位和孔位；关键信息齐全后再建模。', x: 27, y: 55 },
      { label: '生成与导出', text: '检查真实三维模型，明确采用版本后导出 STEP，并接续二维出图。', x: 49, y: 49 },
    ],
  },
  drive: {
    index: '01', label: '模型转工程图', category: '已有模型 → 工程图', title: '从立体，\n到图纸。',
    short: '了解模型转工程图流程', href: '/?tool=step', cta: '打开模型出图工作区',
    description: '导入已有的 STEP 或 STP 模型，检查几何与尺寸信息，按需确认后生成二维工程图。',
    benefit: '保留原有 STEP 出图流程，在工作区查看结果并下载 DXF、SVG、PDF 等产物。',
    image: '/media/drive-blue.png', anchor: { x: 25, y: 48 },
    points: [
      { label: '导入 STEP', text: '选择已有的 STEP 或 STP 文件，在原有工作台上传并检查模型。', x: 32, y: 46 },
      { label: '确认出图信息', text: '选择图幅，查看模型检查结果，按提示确认需要补充的出图信息。', x: 24, y: 49 },
      { label: '生成工程图', text: '由真实模型生成工程图，检查尺寸与视图后下载所需格式。', x: 48, y: 51 },
    ],
  },
  optics: {
    index: '03', label: '照明透镜', category: '光学结构', title: '光线，\n精确成形。',
    short: '了解照明透镜',
    href: '/?tool=step', cta: '打开模型出图工作区',
    description: '概念灯组的光源和透镜示意。此热点未开放。',
    benefit: '仅保留原始素材记录，不代表当前产品能力。',
    image: '/media/lenses-blue.png', anchor: { x: 36, y: 60 },
    points: [
      { label: '投射透镜', text: '透镜用于形成光束。', x: 32.9, y: 55.7 },
      { label: '光源模块', text: '光源位于透镜后方。', x: 50.3, y: 57.2 },
      { label: '散热结构', text: '散热片用于导出热量。', x: 61.9, y: 56.3 },
    ],
  },
} as const
export const systemIds: SystemId[] = ['battery', 'drive', 'optics']
export type HotspotId = SystemId | 'wheels' | 'paint'
export const hotspots: { id: HotspotId; index: string; label: string; anchor: { x: number; y: number }; target: SystemId | null }[] = [
  ...(['drive', 'battery'] as const).map(id => ({ id, index: systems[id].index, label: systems[id].label, anchor: systems[id].anchor, target: id })),
  { id: 'paint', index: '03', label: '图纸建模', anchor: { x: 55, y: 51 }, target: null },
  { id: 'wheels', index: '04', label: '项目与交付', anchor: { x: 48, y: 71 }, target: null },
]

export const workflowEntries = [
  { id: 'drive', index: '01', label: '模型转工程图', input: '已有三维模型', href: '/?tool=step', description: '导入已有模型，检查参考与尺寸，生成可编辑工程图。', action: '导入模型' },
  { id: 'battery', index: '02', label: '文字生成模型', input: '从一段描述开始', href: '/workbench/?mode=text', description: '描述零件与尺寸，补齐关键信息，生成三维模型。', action: '输入描述' },
  { id: 'paint', index: '03', label: '图纸生成模型', input: '从已有图纸开始', href: '/workbench/?mode=drawing', description: '上传图纸，核对识别出的尺寸，继续建模与出图。', action: '上传图纸' },
  { id: 'wheels', index: '04', label: '项目与交付', input: '继续你的设计', href: '/workbench/?view=projects', description: '打开已保存项目，查看历史版本，下载统一交付包。', action: '打开项目' },
] as const

export const workflowMenus = {
  paint: { label: '图纸建模', description: '从 DXF、PDF 或图片开始，核对识别信息并补齐关键尺寸。', href: '/workbench/?mode=drawing', cta: '打开图纸建模工作区', note: '当前支持尺寸清楚、含义明确的简单零件图纸。' },
  wheels: { label: '项目与交付', description: '查看项目与模型历史，确认有效版本，下载统一交付包。', href: '/workbench/?view=projects', cta: '打开项目与交付工作区', note: '版本生效与交付均在工作区明确确认。' },
} as const

// Enable each pair only after first/last frames, crop and color have been checked.
export const clips: Record<SystemId, { forward: string; reverse: string; enabled: boolean }> = {
  battery: { forward: '/media/battery-forward.mp4', reverse: '/media/battery-reverse.mp4', enabled: false },
  drive: { forward: '/media/drive-forward.mp4', reverse: '/media/drive-reverse.mp4', enabled: false },
  optics: { forward: '/media/optics-forward.mp4', reverse: '/media/optics-reverse.mp4', enabled: false },
}
