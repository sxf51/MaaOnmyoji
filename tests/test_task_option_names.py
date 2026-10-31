import json
import tempfile
import unittest
from pathlib import Path
from tools.validate_schema import duplicate_task_options, load_jsonc

ROOT=Path(__file__).resolve().parents[1]

class TaskOptionNames(unittest.TestCase):
    def test_imported_option_keys_are_unique_and_references_resolve(self):
        paths=list((ROOT/'assets/tasks').rglob('*.json'))
        self.assertEqual(duplicate_task_options(paths),[])
        documents=[load_jsonc(p) for p in paths]
        names={name for d in documents for name in d.get('option',{})}
        def walk(value):
            if isinstance(value,dict):
                if isinstance(value.get('option'),list):
                    for reference in value['option']:
                        self.assertIn(reference,names)
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        for d in documents:walk(d)

    def test_options_never_share_a_replaced_custom_param(self):
        # pipeline_override replaces custom_*_param whole; two options writing one would drop keys.
        def params(value,path=()):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key in ('custom_action_param','custom_recognition_param'):yield path+(key,)
                    else:yield from params(child,path+(key,))
        for p in (ROOT/'assets/tasks').rglob('*.json'):
            writers={}
            for name,option in load_jsonc(p).get('option',{}).items():
                for holder in [option,*option.get('cases',[])]:
                    for node,body in holder.get('pipeline_override',{}).items():
                        for path in params(body):
                            writers.setdefault((node,*path),set()).add(name)
            for key,names in writers.items():
                self.assertEqual(len(names),1,(p.name,key,names))

    def test_duplicate_option_definition_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=[]
            for name in ['a','b']:
                p=Path(directory)/(name+'.json')
                p.write_text(json.dumps({'option':{'重名':{}}}),'utf-8')
                paths.append(p)
            result=duplicate_task_options(paths)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0][1],'重名')
