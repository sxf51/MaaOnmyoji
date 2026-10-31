"""Framework routing regressions for selected floors, cleanup and friendly marks.

Uses the same simulated images/inputs as check_native_pipeline; never connects
to a device. Production next/JumpBack/max_hit/child-task contexts execute in SDK.
"""
import tempfile
from pathlib import Path

from check_native_pipeline import ROOT, CountingController, blank_png, run_case
from maa.resource import Resource
from maa.toolkit import Toolkit
from validate_schema import load_jsonc


def main():
    Toolkit.init_option(ROOT/'debug/battle_feature_check')
    nodes={}
    for p in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
        if p.name!='Example.json':nodes.update(load_jsonc(p))
    resource=Resource()
    assert resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded
    (ROOT/'.cache').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
        image=Path(folder)/'screen.png';image.write_bytes(blank_png())
        controller=CountingController(image)
        assert controller.post_connection().wait().succeeded
        def case(*args,**kwargs):return run_case(resource,controller,nodes,*args,**kwargs)
        stage={'PageMitamaStage','MitamaSelectBaqin'}
        selected=stage|{'MitamaLayerSelected'}
        stop_preparation={'MitamaPreparation':{'next':[], 'action':'DoNothing'}}
        case('selected floor is confirmed without another click','MitamaVerifyLayer',selected,
             pipeline_override=stop_preparation)
        case('missed floor click retries before preparation','MitamaVerifyLayer',stage,[
            ('MitamaSelectBaqin',stage),('MitamaSelectBaqin',selected)],pipeline_override=stop_preparation)
        case('three missed floor clicks fail without challenge','MitamaVerifyLayer',stage,
             [('MitamaSelectBaqin',stage)]*3,succeeds=False)
        case('wrong selected floor blocks challenge','MitamaBattleEntry',{'MitamaChallenge','WrongMitamaLayer'},succeeds=False)
        enabled={'CommonBattleMark':{'enabled':True}}
        case('one mark per battle and reset for next victory','MitamaBattleEntry',{'MitamaChallenge'},[
            ('MitamaChallenge',{'PageBattle'}),('CommonBattleMark',{'DailyBattleResult'}),
            ('MitamaBattleResult',{'MitamaFinishOnce','MitamaChallenge'}),
            ('MitamaChallenge',{'PageBattle'}),('CommonBattleMark',{'DailyBattleResult'}),
            ('MitamaBattleResult',{'MitamaFinishOnce'})],requested_count=2,expected_count=2,
            pipeline_override=enabled)
        case('mark never clicks during deployment or preparation','CommonBattleMark',{'PageBattleEntering','DailyPrepare'},
             succeeds=False,pipeline_override=enabled)
        case('mark obeys local input protection','CommonBattleMark',{'PageBattle','TouchAreaRisk'},
             succeeds=False,pipeline_override=enabled)
        buff_on=stage|{'MitamaBuffPanel','MitamaCheckBuffStatusOpen'}
        buff_off=stage|{'MitamaBuffPanel','MitamaCheckBuffStatusClose'}
        case('failed selection closes enabled soul buff and preserves failure','MitamaSession',stage,[
            ('MitamaSelectBaqin',stage),('MitamaSelectBaqin',stage),('MitamaSelectBaqin',buff_on),
            ('MitamaDisableBuffClick',buff_off),('MitamaFinishCloseBuffPanel',stage)],succeeds=False,
            pipeline_override={'MitamaDisableBuff':{'enabled':True}})
        awake={'PageAwakeningStage','AwakeningTargetType','AwakeningLayerText','AwakeningLayerSelected','AwakeningLockedTeam','AwakeningChallengeButton'}
        on=awake|{'AwakeningBuffPanel','AwakeningBuffOn'}
        off=awake|{'AwakeningBuffPanel','AwakeningBuffOff'}
        case('awakening defeat cleans buff without success or drop commit','AwakeningSession',awake,[
            ('AwakeningChallenge',{'DailyBattleFailed','DailyBattleResult'}),
            ('AwakeningFailedBattleResult',on),('AwakeningDisableBuffClick',off),
            ('AwakeningCloseBuffAfterDisable',awake)],succeeds=False,
            pipeline_override={'AwakeningDisableBuff':{'enabled':True}})
        # Clear a resource popup before opening the buff menu; never click purchase.
        case('resource popup is cancelled before awakening buff cleanup','AwakeningAbortCleanup',
             on|{'PageNoResource','NavCancelNoResource'},[
            ('NativeNavCancelNoResource',on),('AwakeningDisableBuffClick',off),
            ('AwakeningCloseBuffAfterDisable',awake)],
             pipeline_override={'AwakeningDisableBuff':{'enabled':True}})
        case('native wait hit budget stops a stuck battle without input','AwakeningAbortCleanup',{'PageBattle'},succeeds=False,
             pipeline_override={'BattleCleanupWait':{'max_hit':2}})
        blocked=on|{'PageNoResource','NavCancelNoResource'}
        case('stuck resource popup stops before touching background buff','AwakeningAbortCleanup',blocked,
             [('NativeNavCancelNoResource',blocked)]*3,succeeds=False,
             pipeline_override={'NativeNavCancelNoResource':{'max_hit':3},
                                'SafeInput_NativeNavCancelNoResource':{'max_hit':3},
                                'AwakeningDisableBuff':{'enabled':True}})
        case('cleanup waits for existing battle and closes result without counting','MitamaAbortCleanup',{'PageBattle'},[
            ('BattleCleanupResult',buff_on),('MitamaDisableBuffClick',buff_off),
            ('MitamaFinishCloseBuffPanel',stage)],
             pipeline_override={'MitamaDisableBuff':{'enabled':True}},
             probe_events=[('PageBattle',{'PageBattle'}),('PageBattle',{'DailyBattleResult'})])
    return 0


if __name__=='__main__':raise SystemExit(main())
