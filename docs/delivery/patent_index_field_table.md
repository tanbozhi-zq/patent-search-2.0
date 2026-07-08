# patent_index 数据库字段表

本文档用于快速浏览 OpenSearch 索引 `patent_index` 的顶层字段含义、mapping 和真实数据形态。字段样例值来自线上索引中的非空样本文档；不同字段的样例可能来自不同专利。长文本和复杂对象已截断展示；明显占位值不会作为文本字段样例。

| 原始字段名 | 中文翻译 | 字段类型 | Mapping | 实际数值样例 |
| --- | --- | --- | --- | --- |
| `patent_id` | 专利文档内部 ID | `keyword` | `{"type":"keyword"}` | `cn-4b4fbfd35abc953a` |
| `PublicationNumber` | 公开号/公告号 | `keyword` | `{"type":"keyword"}` | `CN216242833U` |
| `ApplicationNumber` | 申请号 | `keyword` | `{"type":"keyword"}` | `CN200810063730.0` |
| `PublicationDate` | 公开日/公告日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `2008-12-31` |
| `ApplicationDate` | 申请日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `2008-07-28` |
| `Kind` | 文献类型码 | `keyword` | `{"type":"keyword"}` | `U1` |
| `Type` | 专利类型 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `发明专利` |
| `PatentTypeCode` | 专利类型代码 | `keyword` | `{"type":"keyword"}` | `1` |
| `Country` | 国家/地区 | `keyword` | `{"type":"keyword"}` | `CN` |
| `AuthorityCountry` | 受理局/公开局国家地区 | `keyword` | `{"type":"keyword"}` | `DE` |
| `PublicationCountry` | 公开号国家地区 | `keyword` | `{"type":"keyword"}` | `DE` |
| `ApplicantCountry` | 申请人国家地区 | `keyword` | `{"type":"keyword"}` | `JP` |
| `OriginalLanguage` | 原始语言 | `keyword` | `{"type":"keyword"}` | `ger` |
| `PublicationNumberAliases` | 公开号/公告号别名 | `keyword` | `{"type":"keyword"}` | `["DE7605710U1"]` |
| `ApplicationNumberAliases` | 申请号别名 | `keyword` | `{"type":"keyword"}` | `["DE7605710"]` |
| `FirstPublicationNumber` | 首次公开号 | `keyword` | `{"type":"keyword"}` | `KR19880008554U` |
| `FirstPublicationDate` | 首次公开日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `1976-06-24` |
| `GrantPublicationNumber` | 授权公告号 | `keyword` | `{"type":"keyword"}` | `DE7605710U1` |
| `Title` | 标题/名称 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `一种治疗跌打伤痛的外用中药制剂及其制备方法` |
| `TitleCN` | 中文标题 | `text` | `{"type":"text","analyzer":"standard"}` | `制造电机的插入线圈的装置的方法和夹持装置制造装置` |
| `TitleEN` | 英文标题 | `text` | `{"type":"text","analyzer":"standard"}` | `Heterocyclic compounds, manufacturing method thereof and pharmaceutical compositions containing the` |
| `TitleOriginal` | 原文标题 | `text` | `{"type":"text","analyzer":"standard"}` | `Vorrichtung zur photographischen Aufzeichnung von Schallwellen auf einen lichtempfindlichen Traeger` |
| `Abstract` | 摘要 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `本发明提供一种能使UV墨以不会彼此渗透的方式附着、且在平滑化的状态下硬化的喷墨打印机。喷墨打印机(10)包括:导轨(15b),与台板(12a)相对,沿左右方向延伸地设置;打印单元(20),其具有:设置为能沿导轨(15b…` |
| `AbstractCN` | 中文摘要 | `text` | `{"type":"text","analyzer":"standard"}` | `[目的]药学上有用的环状化合物,醛糖还原酶-具有的抑制效果在所述一般式(I)化合物和它们的制造方法。和提供药物组合物。[构造成]点击的一般式(II)的酮化合物与间隙的化学化合物的一般式(III)反应,该式(i)用于制备…` |
| `AbstractEN` | 英文摘要 | `text` | `{"type":"text","analyzer":"standard"}` | `The present invention relates to a method for operating a winding machine (27), wherein during a rewinding pr…` |
| `AbstractOriginal` | 原文摘要 | `text` | `{"type":"text","analyzer":"standard"}` | `Die vorliegende Erfindung betrifft ein Verfahren zum Betreiben einer Spulmaschine (27), wobei während eines U…` |
| `AbstractFigure` | 摘要附图对象 | `object` | `{"type":"object","properties":{"file":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"he":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgContent":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgFormat":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"wi":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}` | `[{"wi":"661","imgFormat":"TIFF","file":"200410082029.TIF","he":"807.044189"}]` |
| `AbstractFigureUrl` | 摘要附图 URL | `keyword` | `{"type":"keyword"}` | `http://pt.cnipr.com/static/bce81273d1936009a6d18f0f523c0cf7/371B4D/f59547/2800829630/1751959739/1200/pi12/PAT…` |
| `Applicant` | 申请人 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `安徽智界新能源汽车有限公司;奇瑞汽车股份有限公司` |
| `ApplicantNormalized` | 规范化申请人 | `text` | `{"type":"text","analyzer":"standard"}` | `Kj Innovation Co., Ltd.` |
| `FirstApplicant` | 第一申请人 | `text` | `{"type":"text","analyzer":"standard"}` | `Kj Innovation Co., Ltd.` |
| `ApplicantAddress` | 申请人地址 | `keyword` | `{"type":"keyword"}` | `广东省广州市花都区迎宾大道金枫二街19号802房` |
| `ApplicantPostCode` | 申请人邮编 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `510000` |
| `ApplicantProvince` | 申请人省份 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `广东省` |
| `ApplicantCity` | 申请人城市 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `黔西南布依族苗族自治州` |
| `ApplicantCounty` | 申请人区县 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `花都区` |
| `Assignee` | 当前权利人/受让人 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `安徽智界新能源汽车有限公司;奇瑞汽车股份有限公司` |
| `AssigneeNormalized` | 规范化当前权利人 | `text` | `{"type":"text","analyzer":"standard"}` | `Kj Innovation Co., Ltd.` |
| `AssigneeSource` | 当前权利人来源 | `keyword` | `{"type":"keyword"}` | `applicant_fallback` |
| `AssigneeAddress` | 当前权利人地址 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `["江苏省常州市武进区常武中路18号常州科教城江南现代工业研究院"]` |
| `AssigneeProvince` | 当前权利人省份 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `["四川省"]` |
| `AssigneeCity` | 当前权利人城市 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `["恩施土家族苗族自治州"]` |
| `AssigneeCounty` | 当前权利人区县 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `["科尔沁区"]` |
| `Inventor` | 发明人/设计人 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `山本幸祐;间中三郎;高仓昭;小笠原健治;佐久本和实;加藤一雄;本村京志;长谷川贵则` |
| `FirstInventor` | 第一发明人 | `text` | `{"type":"text","analyzer":"standard"}` | `Oscar Wangerin Elmer` |
| `Agent` | 代理人 | `text` | `{"type":"text","analyzer":"ik_max_word"}` | `张智平;蔡正保` |
| `Agency` | 代理机构 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `郑州华智星知识产权代理事务所(普通合伙)` |
| `AgencyNumber` | 代理机构代码 | `keyword` | `{"type":"keyword"}` | `44247` |
| `AgencyRaw` | 代理机构原始值 | `keyword` | `{"type":"keyword"}` | `北京风雅颂专利代理有限公司` |
| `Examiner` | 审查员 | `text` | `{"type":"text"}` | `["张东丽"]` |
| `IPC` | 主 IPC 分类号 | `keyword` | `{"type":"keyword"}` | `B25B7/00(2006.01)` |
| `IPCList` | IPC 分类号列表 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `["B02C4/08(2006.01)I","B02C4/30(2006.01)I","B02C4/42(2006.01)I","B02C4/28(2006.01)I"]` |
| `IPCSection` | IPC 部 | `keyword` | `{"type":"keyword"}` | `B` |
| `IPCLargeCategory` | IPC 大类 | `keyword` | `{"type":"keyword"}` | `B25` |
| `IPCSmallCategory` | IPC 小类 | `keyword` | `{"type":"keyword"}` | `B25B` |
| `IPCLargeGroup` | IPC 大组 | `keyword` | `{"type":"keyword"}` | `B25B7/00` |
| `IPCSmallGroup` | IPC 小组 | `keyword` | `{"type":"keyword"}` | `C02F3/34` |
| `IPCDetails` | IPC 明细对象 | `flat_object` | `{"type":"flat_object"}` | `未取到非空样本` |
| `CPCList` | CPC 分类号列表 | `keyword` | `{"type":"keyword"}` | `["A47C17/16"]` |
| `CPCMain` | 主 CPC 分类号 | `keyword` | `{"type":"keyword"}` | `A47C17/16` |
| `CPCSection` | CPC 部 | `keyword` | `{"type":"keyword"}` | `A` |
| `CPCLargeCategory` | CPC 大类 | `keyword` | `{"type":"keyword"}` | `A47` |
| `CPCSmallCategory` | CPC 小类 | `keyword` | `{"type":"keyword"}` | `A47C` |
| `CPCLargeGroup` | CPC 大组 | `keyword` | `{"type":"keyword"}` | `G11B33;G11B15` |
| `CPCSmallGroup` | CPC 小组 | `keyword` | `{"type":"keyword"}` | `A47C17/16` |
| `ECLAList` | ECLA 分类号列表 | `keyword` | `{"type":"keyword"}` | `["H04L12/56F1","H04L12/64B","H04L29/06H","H04M7/00M16"]` |
| `ECLASection` | ECLA 部 | `keyword` | `{"type":"keyword"}` | `A` |
| `ECLALargeCategory` | ECLA 大类 | `keyword` | `{"type":"keyword"}` | `A47` |
| `ECLASmallCategory` | ECLA 小类 | `keyword` | `{"type":"keyword"}` | `H04M;H04L` |
| `ECLALargeGroup` | ECLA 大组 | `keyword` | `{"type":"keyword"}` | `A47C17` |
| `ECLASmallGroup` | ECLA 小组 | `keyword` | `{"type":"keyword"}` | `A47C17/16` |
| `Priority` | 优先权信息 | `flat_object` | `{"type":"flat_object"}` | `[{"ApplicationNumber":"2009.02.20 JP 2009-038142"},{"ApplicationNumber":"2009.12.08 JP 2009-278879"}]` |
| `PCT` | PCT 综合信息 | `flat_object` | `{"type":"flat_object"}` | `未取到非空样本` |
| `PCTApplicationNumber` | PCT 申请号 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `PCT/JP2010/000047` |
| `PCTApplicationDate` | PCT 申请日 | `date` | `{"type":"date"}` | `2024-07-31` |
| `PCTApplicationCountry` | PCT 申请国家/局 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `未取到非空样本` |
| `PCTPublicationNumber` | PCT 公开号 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `2025/058237` |
| `PCTPublicationNumberRaw` | PCT 公开号原始值 | `keyword` | `{"type":"keyword"}` | `WO2025/058237` |
| `PCTPublicationDate` | PCT 公开日 | `date` | `{"type":"date"}` | `2025-03-20` |
| `PCTPublicationLanguage` | PCT 公开语言 | `keyword` | `{"type":"keyword"}` | `JA` |
| `NationalStageEntryDate` | 进入国家阶段日 | `keyword` | `{"type":"keyword"}` | `2011-03-15` |
| `FamilyNo` | 专利族号 | `keyword` | `{"type":"keyword"}` | `ZH_CN2022216242833U` |
| `Family` | 同族专利列表 | `keyword` | `{"type":"keyword"}` | `["['CN101814886A', 'US2010220556(A1)']"]` |
| `SameApplicationNumber` | 同申请号文献 | `keyword` | `{"type":"keyword"}` | `CN201911063791.1` |
| `FamilyCountries` | 同族国家地区 | `keyword` | `{"type":"keyword"}` | `["DE"]` |
| `SimpleFamily` | 简单同族成员 | `keyword` | `{"type":"keyword"}` | `["KR19900007323Y1","KR19880008554U","KR19900052502Y1"]` |
| `SimpleFamilyId` | 简单同族 ID | `keyword` | `{"type":"keyword"}` | `42351882` |
| `SimpleFamilyCount` | 简单同族数量 | `integer` | `{"type":"integer"}` | `3` |
| `ExtendedFamily` | 扩展同族成员 | `keyword` | `{"type":"keyword"}` | `["DE7605710U1"]` |
| `ExtendedFamilyId` | 扩展同族 ID | `keyword` | `{"type":"keyword"}` | `42157942` |
| `ExtendedFamilyCount` | 扩展同族数量 | `integer` | `{"type":"integer"}` | `1` |
| `DocDBFamily` | DocDB 同族成员 | `keyword` | `{"type":"keyword"}` | `["DE7605710U1"]` |
| `DocDBFamilyId` | DocDB 同族 ID | `keyword` | `{"type":"keyword"}` | `6662480` |
| `DocDBFamilyCount` | DocDB 同族数量 | `integer` | `{"type":"integer"}` | `1` |
| `MainClaim` | 主权利要求/首项权利要求 | `text` | `{"type":"text","analyzer":"standard"}` | `1.一种废料易收集的五金配件焊接装置,其特征在于,包括底板(1),所述底板(1)的顶端设有机框(2),所述机框(2)内部的一端设有承载台(16),所述机框(2)内侧的底板(1)顶端放置有接料框(19),所述承载台(16…` |
| `MainClaimLength` | 首项权利要求长度 | `integer` | `{"type":"integer"}` | `95` |
| `ClaimCount` | 权利要求总数 | `integer` | `{"type":"integer"}` | `4` |
| `IndependentClaimCount` | 独立权利要求数量 | `integer` | `{"type":"integer"}` | `0` |
| `DependentClaimCount` | 从属权利要求数量 | `integer` | `{"type":"integer"}` | `0` |
| `IndependentClaimsCN` | 中文独立权利要求全文 | `text` | `{"type":"text","analyzer":"standard"}` | `未取到非空样本` |
| `DependentClaimsCN` | 中文从属权利要求全文 | `text` | `{"type":"text","analyzer":"standard"}` | `未取到非空样本` |
| `Instructions` | 说明书正文 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `技术领域<br/>本发明涉及图像采集技术领域。更具体地，本发明涉及一种口腔数字印模仪图像采集方法及系统。<br/>背景技术<br/>口腔数字印模仪通过激光扫描、光学扫描等技术获取口腔结构的三维数据，将传统的牙颌石膏模型…` |
| `Requirement` | 完整权利要求书正文 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `1.一种口腔数字印模仪图像采集方法，其特征在于，包括：通过口腔数字印模仪采集口腔图像；对口腔图像进行多次下采样，根据各下采样图像与口腔图像的大小比值，获得各下采样图像的权重；根据权重对各下采样图像中各灰度值的频率加权，…` |
| `Drawings` | 附图信息 | `object` | `{"type":"object","properties":{"figureLabels":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"file":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"he":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgContent":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgFormat":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"inline":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"num":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"orientation":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"wi":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}` | `[{"imgFormat":"JPEG","orientation":"portrait","file":"FT_1.JPG","inline":"no","num":"0001","figureLabels":"图1…` |
| `DescriptionImages` | 说明书附图/图片资源 | `object` | `{"type":"object","properties":{"file":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"he":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgContent":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"imgFormat":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"inline":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"orientation":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"wi":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}` | `[{"imgFormat":"JPEG","orientation":"portrait","file":"SMS_2.JPG","inline":"yes"},{"imgFormat":"JPEG","orienta…` |
| `PatentImage` | 专利图片 | `keyword` | `{"type":"keyword"}` | `未取到非空样本` |
| `PatentImages` | 专利图片列表 | `keyword` | `{"type":"keyword"}` | `未取到非空样本` |
| `PatentImagesFull` | 完整专利图片资源 | `flat_object` | `{"type":"flat_object"}` | `未取到非空样本` |
| `PDFFiles` | PDF 文件资源 | `flat_object` | `{"type":"flat_object"}` | `未取到非空样本` |
| `ReferencesCited` | 引证文献结构 | `object` | `{"type":"object","properties":{"Country":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"Date":{"type":"date"},"DocNumber":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}},"Kind":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}` | `[{"DocNumber":"111871620","Kind":"A","Country":"CN","Date":"2020-11-03"},{"DocNumber":"111871620","Kind":"A",…` |
| `ReferencesCitedRaw` | 引证文献原始文本 | `text` | `{"type":"text","analyzer":"standard"}` | `WO 2005/040849 A2,2005.05.06,说明书13-17段、权利要求1,9,11、图1.;WO 2005/040849 A2,2005.05.06,说明书13-17段、权利要求1,9,11、图1.;U…` |
| `ReferencesCitedText` | 引证文献文本 | `text` | `{"type":"text","analyzer":"standard"}` | `['CN 102116155 A,2011.07.06,', 'RU 2162150 C1,2001.01.20,', 'GB 1050201 A,', 'CN 101526001 A,2009.09.09,', 'R…` |
| `RelatedDocuments` | 相关文档 | `flat_object` | `{"type":"flat_object"}` | `[{"DocNumber":"119540469","Kind":"A","Country":"CN","Date":"2025-02-28"}]` |
| `RelatedObligee` | 相关权利人 | `text` | `{"type":"text","analyzer":"standard"}` | `['福州启联信息咨询有限公司', '重庆邮电大学', '深圳占领信息技术有限公司', '西安龙合林创知识产权代理有限公司']` |
| `LegalStatus` | 法律状态 | `keyword` | `{"type":"keyword"}` | `2018.08.17#未缴年费专利权终止;2011.02.16#授权;2009.05.20#实质审查的生效;2008.12.31#公开` |
| `LatestLegalStatus` | 最新法律状态 | `keyword` | `{"type":"keyword"}` | `未缴年费专利权终止` |
| `LegalStatusCode` | 法律状态代码 | `keyword` | `{"type":"keyword"}` | `20` |
| `LegalStatusHistory` | 法律状态历史 | `flat_object` | `{"type":"flat_object"}` | `未取到非空样本` |
| `GrantDate` | 授权日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `2011-02-16` |
| `ExpireDate` | 失效/届满日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `2018-08-17` |
| `LastUpdateLegalDate` | 法律状态最后更新日 | `date` | `{"type":"date","format":"yyyy-MM-dd\|\|yyyyMMdd\|\|strict_date_optional_time"}` | `2024-03-13` |
| `GazetteNumber` | 公报号 | `text` | `{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}` | `41-1802` |
| `GazetteDate` | 公报日 | `date` | `{"type":"date"}` | `2025-05-02` |
| `SourceRecordId` | 来源记录 ID | `keyword` | `{"type":"keyword"}` | `5c01024f7094037b128d197b` |
| `SourcePatentId` | 来源专利 ID | `keyword` | `{"type":"keyword"}` | `FMZL@CN101332589A` |
| `SourceMeta` | 来源扩展元数据 | `flat_object` | `{"type":"flat_object"}` | `{"appResource":"国家","gazettePath":"XX/2008/20081231","tifDistributePath":"BOOKS/FM/2008/20081231/200810063730…` |

## 样例数据

以下为线上索引中的 5 条真实专利样例，只保留常用核心字段；摘要等长文本已截断。

```json
[
  {
    "patent_id": "cn-cd07d7245ca0cd1a",
    "PublicationNumber": "CN119188170B",
    "ApplicationNumber": "CN202411108082.1",
    "PublicationDate": "2026-06-12",
    "ApplicationDate": "2024-08-13",
    "Type": "发明专利",
    "Title": "一种轴承座壳体的加工工艺",
    "Abstract": "本发明公开了一种轴承座壳体的加工工艺,包括以下步骤:S1.铣第一基准面:采用第一夹具将轴承座壳体固定在机床上,第一基准面朝上且加工余量为0.5mm,加工进给为≥0.2mm/r,加工转速为≥7000r/min;S2.依次进行粗、精铰第一基准孔及阶梯孔,第一基准孔及阶梯孔的加工余量为单边0.4mm;S3.铰第二基准孔;先粗铰第二基准孔,再在第二基准孔内壁的上端…",
    "Applicant": "湛江德利车辆部件有限公司",
    "Inventor": "薛敏海;陈明伟;魏清亮;肖文玲;陈心威",
    "IPC": "B23P15/00",
    "IPCList": [
      "B23P15/00",
      "B23Q3/00"
    ],
    "LegalStatus": "2026.06.12#授权;2025.01.14#实质审查的生效;2024.12.27#公开",
    "LatestLegalStatus": "授权",
    "SourcePatentId": "FMSQ@CN119188170B"
  },
  {
    "patent_id": "cn-5eda6d4921c8a70a",
    "PublicationNumber": "CN119188276B",
    "ApplicationNumber": "CN202411458278.3",
    "PublicationDate": "2026-06-12",
    "ApplicationDate": "2024-10-17",
    "Type": "发明专利",
    "Title": "一种汽车顶蓬天窗的安装生产线及其安装方法",
    "Abstract": "本发明提出了一种汽车顶蓬天窗的安装生产线,包括:定位工装、尾沿卡座预埋工装、第一机器人以及第二机器人;所述定位工装水平安装在地面上,所述尾沿卡座预埋工装安装在定位工装的上方,所述第一机器人和第二机器人均于所述定位工装相邻设置,且所述第一机器人和第二机器人运动时不相互干涉;可以结合现有人员作业,实现人机分离的作业方式,规避人员作业安全隐患,消除人工/设备等待…",
    "Applicant": "湖北吉兴汽车部件有限公司",
    "Inventor": "闻伦;钱怡敏;郭坤;郝晓东",
    "IPC": "B23P21/00",
    "IPCList": [
      "B23P21/00",
      "B62D65/02",
      "B62D65/06",
      "B62D65/14"
    ],
    "LegalStatus": "2026.06.12#授权;2025.03.21#实质审查的生效;2024.12.27#公开",
    "LatestLegalStatus": "授权",
    "SourcePatentId": "FMSQ@CN119188276B"
  },
  {
    "patent_id": "cn-3890ad80fcd1e815",
    "PublicationNumber": "CN119188508B",
    "ApplicationNumber": "CN202411560172.4",
    "PublicationDate": "2026-06-12",
    "ApplicationDate": "2024-11-04",
    "Type": "发明专利",
    "Title": "一种光学元器件调节偏心的方法",
    "Abstract": "本发明提供了一种光学元器件调节偏心的方法,属于偏心调节技术领域。本发明包括如下步骤:步骤(1)、设计V槽并固定尺寸;步骤(2)、光学元器件的二个面粘结,背面贴合工装背面,正面贴合靠体的棱线;步骤(3)、通过看背面光圈确认光学元器件放置在靠体上贴合的紧凑性,并用胶水封边缘;步骤(4)、放置对应的V槽固定后,进行对应的产品上表面磨削,把多余的部分去除;步骤(5…",
    "Applicant": "福建福特科光电股份有限公司",
    "Inventor": "陈昕;魏德全;阮异凯",
    "IPC": "B24B13/00",
    "IPCList": [
      "B24B13/00",
      "B24B13/005",
      "B24B55/00",
      "B24B47/22",
      "B24B1/00"
    ],
    "LegalStatus": "2026.06.12#授权;2025.01.14#实质审查的生效;2024.12.27#公开",
    "LatestLegalStatus": "授权",
    "SourcePatentId": "FMSQ@CN119188508B"
  },
  {
    "patent_id": "cn-abc8e665f4f9fc0a",
    "PublicationNumber": "CN119195966B",
    "ApplicationNumber": "CN202411305299.1",
    "PublicationDate": "2026-06-12",
    "ApplicationDate": "2024-09-19",
    "Type": "发明专利",
    "Title": "一种用于抽水蓄能机组保护调试的快速工况切换装置",
    "Abstract": "本发明公开了一种用于抽水蓄能机组保护调试的快速工况切换装置,包括总线、空气开关、熔断器、按钮组件、按钮组件直接控制的第一继电器组、联动触点组、联动触点组控制的第二继电器组、第二继电器组的触点所连接的接线孔板,所述的按钮组件包括对应发电工况的第一按钮SB1、对应调相工况的第二按钮SB2、对应发电启动工况的第三按钮SB3、对应抽水工况的第四按钮SB4、对应抽水…",
    "Applicant": "国网新源集团有限公司;河南国网宝泉抽水蓄能有限公司",
    "Inventor": "樊京伟;方书博;陈昌山;臧克佳;霍献东;吴小锋;马聖恒;段乐乐;段嘉屹",
    "IPC": "F03B15/00",
    "IPCList": [
      "F03B15/00",
      "H02H7/00",
      "F03B3/10",
      "H01H13/04"
    ],
    "LegalStatus": "2026.06.12#授权;2025.01.14#实质审查的生效;2024.12.27#公开",
    "LatestLegalStatus": "授权",
    "SourcePatentId": "FMSQ@CN119195966B"
  },
  {
    "patent_id": "cn-528a7d0659261b95",
    "PublicationNumber": "CN119199536B",
    "ApplicationNumber": "CN202411039791.9",
    "PublicationDate": "2026-06-12",
    "ApplicationDate": "2024-07-31",
    "Type": "发明专利",
    "Title": "一种燃料电池堆衰减过程片间相互影响的评估方法",
    "Abstract": "本发明涉及一种燃料电池堆衰减过程片间相互影响的评估方法,方法包括以下步骤:S1、获取实际电压数据和实际参数数据;S2、将电压数据输入第一时空图神经网络模型,得到第一电压预测值;S3、将参数数据输入第二时空图神经网络模型,得到第二电压预测值;S4、采用SHAP分析法,基于所述第一电压预测值和第二电压预测值得到多个样本的局部Shapley值,基于多个样本的局部…",
    "Applicant": "同济大学",
    "Inventor": "陈会翠;戴隶辰",
    "IPC": "G01R31/367",
    "IPCList": [
      "G01R31/367",
      "G01R31/378",
      "G06N3/042",
      "G06N3/08",
      "G06N3/0464",
      "G06F18/2415",
      "G06F18/214"
    ],
    "LegalStatus": "2026.06.12#授权;2025.01.14#实质审查的生效;2024.12.27#公开",
    "LatestLegalStatus": "授权",
    "SourcePatentId": "FMSQ@CN119199536B"
  }
]
```
## 完整字段样例

下面是一条线上真实专利的完整 `_source` 样例。这里的“完整”指该文档实际拥有的所有顶层字段，不代表 126 个 mapping 字段都会在每条文档中出现；超长文本、数组和图片正文已截断或排除，避免文档过大。

```json
{
  "patent_id": "cn-53a480502e78a1cf",
  "Abstract": "本发明的供给热量推定方法是根据供给至高炉内的热量和高炉内的熔融生铁的制造速度来推定供给至高炉内的生铁的热量的供给热量推定方法，其包括：推定步骤，其中，推定由炉内通过气体引起的带出显热的变化和由被炉内通过气体预热的原料供给的带入显热的变化，考虑推定出的带出显热和带入显热的变化来推定供给至高炉内的生铁的热量。",
  "AbstractFigure": [
    {
      "wi": "422",
      "imgFormat": "JPEG",
      "file": "202180092177.JPG",
      "he": "1000"
    }
  ],
  "Agency": "中原信达知识产权代理有限责任公司",
  "AgencyNumber": "11219",
  "Agent": [
    "满凤",
    "金龙河"
  ],
  "Applicant": [
    "杰富意钢铁株式会社"
  ],
  "ApplicantAddress": "日本东京",
  "ApplicantCity": "0",
  "ApplicantCounty": "0",
  "ApplicantPostCode": "",
  "ApplicantProvince": "日本",
  "ApplicationDate": "2021-11-17",
  "ApplicationNumber": "CN202180092177.7",
  "Assignee": "杰富意钢铁株式会社",
  "AssigneeAddress": [
    "日本东京"
  ],
  "AssigneeCity": [
    "0"
  ],
  "AssigneeCounty": [
    "0"
  ],
  "AssigneeProvince": [
    "0"
  ],
  "AssigneeSource": "patentee",
  "Country": "JP",
  "DescriptionImages": [
    {
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "SMS_1.JPG",
      "inline": "no"
    },
    {
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "SMS_2.JPG",
      "inline": "no"
    },
    {
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "SMS_3.JPG",
      "inline": "no"
    }
  ],
  "Drawings": [
    {
      "wi": "1000",
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "HDA0004361493560000011.JPG",
      "inline": "no",
      "num": "0001",
      "figureLabels": "图1",
      "he": "515"
    },
    {
      "wi": "424",
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "HDA0004361493560000021.JPG",
      "inline": "no",
      "num": "0002",
      "figureLabels": "图2",
      "he": "1000"
    },
    {
      "wi": "572",
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "HDA0004361493560000031.JPG",
      "inline": "no",
      "num": "0003",
      "figureLabels": "图3",
      "he": "1000"
    },
    {
      "wi": "1000",
      "imgFormat": "JPEG",
      "orientation": "portrait",
      "file": "HDA0004361493560000041.JPG",
      "inline": "no",
      "num": "0004",
      "figureLabels": "图4",
      "he": "920"
    }
  ],
  "Examiner": [
    "谢哲宇"
  ],
  "GazetteDate": "2026-03-06",
  "GazetteNumber": "42-1002",
  "GrantDate": "2026-03-06",
  "IPC": "C21B5/00",
  "IPCLargeCategory": "C21",
  "IPCLargeGroup": "C21B5/00",
  "IPCList": [
    "C21B 5/00 (2006.01)"
  ],
  "IPCSection": "C",
  "IPCSmallCategory": "C21B",
  "Instructions": "技术领域<br/>本发明涉及推定供给至高炉内的生铁的热量的供给热量推定方法、供给热量推定装置和高炉的操作方法。<br/>背景技术<br/>通常，为了稳定地操作高炉，需要将熔融生铁温度维持在规定范围内。详细而言，熔融生铁温度处于低位时，熔融生铁以及与熔融生铁一起生成的熔渣的粘性升高，难以使熔融生铁、熔渣从出铁口排出。另一方面，熔融生铁温度处于高位时，熔融生铁中的Si浓度升高而熔融生铁的粘性升高，因此熔融生铁粘在风口而使风口熔损的风险变高。因此，为了稳定地操作高炉，需要抑制熔融生铁温度的变动。出于这样的背景，提出了推定供给至高炉内的热量、熔融生铁温度的各种方法。具体而言，专利文献1中公开了一种高炉的炉热控制方法，其特征在于，根据自对应于目标熔融生铁温度的炉热指数基准水平起的当前时刻的炉热指数位移量、自对应于目标熔融...<已截断>",
  "Inventor": [
    "市川和平",
    "山本哲也",
    "佐藤健",
    "川尻雄基"
  ],
  "LatestLegalStatus": "授权",
  "LegalStatus": "2026.03.06#授权;2023.10.17#实质审查的生效;2023.09.26#公开",
  "LegalStatusCode": "10",
  "MainClaim": "1.一种供给热量推定方法,其是根据供给至高炉内的热量和高炉内的熔融生铁的制造速度来推定供给至高炉内的生铁的热量的供给热量推定方法,其包括: 推定步骤,其中,推定由炉内通过气体引起的带出显热的变化和由被所述炉内通过气体预热的原料供给的带入显热的变化,考虑推定出的带出显热和带入显热的变化来推定供给至高炉内的生铁的热量,所述推定步骤包括算出所述带出显热的步骤,其中,通过将在所述高炉的风口前燃烧的气体的推定温度与表示高炉炉下部上端的温度的基准温度的温度差乘以所述炉内通过气体的比热而算出所述带出显热。",
  "NationalStageEntryDate": "2023-07-27",
  "PCTApplicationCountry": "JP",
  "PCTApplicationDate": "2021-11-17",
  "PCTApplicationNumber": "PCT/JP2021/042183",
  "PCTPublicationDate": "2022-08-11",
  "PCTPublicationLanguage": "ja",
  "PCTPublicationNumber": "2022/168396",
  "PCTPublicationNumberRaw": "WO2022/168396",
  "PatentTypeCode": "1",
  "Priority": [
    {
      "ApplicationNumber": "2021.02.05 JP2021017421"
    }
  ],
  "PublicationDate": "2026-03-06",
  "PublicationNumber": "CN116806270B",
  "ReferencesCited": [
    {
      "DocNumber": "2018145520",
      "Kind": "A",
      "Country": "JP",
      "Date": "2018-09-20"
    },
    {
      "DocNumber": "2018145520",
      "Kind": "A",
      "Country": "JP",
      "Date": "2018-09-20"
    }
  ],
  "RelatedDocuments": [
    {
      "DocNumber": "116806270",
      "Kind": "A",
      "Country": "CN",
      "Date": "2023-09-26"
    }
  ],
  "Requirement": "1.一种供给热量推定方法，其是根据供给至高炉内的热量和高炉内的熔融生铁的制造速度来推定供给至高炉内的生铁的热量的供给热量推定方法，其包括：推定步骤，其中，推定由炉内通过气体引起的带出显热的变化和由被所述炉内通过气体预热的原料供给的带入显热的变化，考虑推定出的带出显热和带入显热的变化来推定供给至高炉内的生铁的热量，所述推定步骤包括算出所述带出显热的步骤，其中，通过将在所述高炉的风口前燃烧的气体的推定温度与表示高炉炉下部上端的温度的基准温度的温度差乘以所述炉内通过气体的比热而算出所述带出显热。<br/>2.根据权利要求1所述的供给热量推定方法，其中，所述推定步骤包含如下步骤：推定存在于所述高炉中的炉芯焦炭所保持的热量，考虑推定出的炉芯焦炭所保持的热量来推定供给至高炉内的生铁的热量。<br/>3.一种供给热量推定装置...<已截断>",
  "SourceMeta": {
    "appResource": "国际",
    "insertTime": "2026-03-09 15:40:19",
    "proCode": "日本;JP",
    "nec": "[\"E4872\",\"E4873\",\"C3516\",\"E4874\",\"E4875\",\"E4871\",\"C3110\",\"E4879\",\"C4330\",\"E4861\",\"E4840\",\"E4862\",\"E4863\",\"C4210\"]",
    "patentee": "杰富意钢铁株式会社",
    "lastUpdateTime": "2026-03-09 17:30:13"
  },
  "SourcePatentId": "FMSQ@CN116806270B",
  "SourceRecordId": "69ae7963fb7c4f232c269c25",
  "Title": "供给热量推定方法、供给热量推定装置和高炉的操作方法",
  "Type": "发明专利"
}
```
