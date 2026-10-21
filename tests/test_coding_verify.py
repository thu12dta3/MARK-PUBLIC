
import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mark.coding_verify import check,code_blocks
from mark.coding_assistant import instructions,verify_reply,footer,languages

FIXTURES={'python':'def add(a: int, b: int) -> int:\n    return a + b\n',
          'c':'int add(int a,int b){return a+b;}\n',
          'cpp':'#include <vector>\nint main(){std::vector<int> v{1,2};return v.size()==2?0:1;}\n',
          'kotlin':'fun main() { val names = listOf("MARK"); println(names.first()) }\n',
          'assembly':'.text\n.global sum\nsum:\n mov %edi,%eax\n add %esi,%eax\n ret\n',
          'aarch64':'.text\n.global sum\nsum:\n add w0,w0,w1\n ret\n'}

class CodingVerifierTests(unittest.TestCase):
    def test_all_installed_compilers_valid_fixtures(self):
        results=[]
        for language,code in FIXTURES.items():
            with self.subTest(language=language):
                result=check(code,language);self.assertEqual(result['status'],'PASS',result);self.assertFalse(result['executed']);results.append({k:v for k,v in result.items() if k!='diagnostic'})
        print('CODING_COMPILER_METRICS '+json.dumps(results),flush=True)
    def test_real_invalid_syntax_rejected(self):
        for language,code in [('python','def broken(:\n'),('c','int main( { bad; }'),('cpp','#include <vector>\nint main( { bad; }')]:
            with self.subTest(language=language):self.assertEqual(check(code,language)['status'],'FAIL')
    def test_never_executes_module_or_constructor(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'must-not-exist'
            code=f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
            self.assertEqual(check(code,'python')['status'],'PASS');self.assertFalse(marker.exists())
    def test_private_home_not_visible_to_preprocessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret=Path(tmp)/'private.h';secret.write_text('int secret;')
            result=check(f'#include "{secret}"\nint main(void){{return 0;}}','c');self.assertNotEqual(result['status'],'PASS')
    def test_no_host_fallback_without_sandbox(self):
        with patch('mark.coding_verify.shutil.which',return_value=None),patch('mark.coding_verify.subprocess.Popen') as launch:
            self.assertEqual(check('print(1)','python')['status'],'UNAVAILABLE');launch.assert_not_called()
    def test_bounds_and_unknown_language(self):
        with self.assertRaises(ValueError):check('x'*64001,'python')
        with self.assertRaises(ValueError):check('x','shell')
    def test_instruction_selection_and_optout(self):
        self.assertEqual(languages('Olá, tudo bem?'),[]);self.assertIn('c',languages('Crie um programa em C'))
        self.assertIn('ownership',instructions('explique RAII em C++',{}));self.assertEqual(instructions('Python',{'coding':{'enabled':False}}),'')
    def test_answer_validation_is_truthful_and_explicit_scope(self):
        result=verify_reply('```python\ndef add(a,b):\n    return a+b\n```','Escreva Python',{})
        self.assertEqual(result[0]['status'],'PASS');self.assertIn('não foi executado',footer(result))
        with patch('mark.coding_assistant.check') as call:self.assertEqual(verify_reply('```python\nprint(1)\n```','oi',{}),[]);call.assert_not_called()
    def test_brain_adds_language_discipline_and_real_check_footer(self):
        from mark.brain import answer
        from mark.memory import ShortMemory
        cfg={'coding':{'enabled':True},'cognition':{'enabled':True},'web':{'enabled':False},'memory':{'max_messages':6}}
        with patch('mark.brain.route',return_value={'vision':False,'web':False}),patch('mark.brain._ollama',return_value='```python\ndef add(a,b):\n    return a+b\n```') as model:
            result=answer('Crie uma função em Python para somar',cfg,ShortMemory());self.assertIn('compilação/sintaxe verificada',result);self.assertIn('rigor de engenharia',model.call_args.args[2]);self.assertIn('não foi executado',result)


    def test_bounded_repair_uses_actual_errors_and_preserves_functions(self):
        from mark.coding_assistant import review_reply
        from unittest.mock import Mock
        broken='```python\ndef add(a,b)\n    return a+b\n```'
        fixed='```python\ndef add(a,b):\n    return a+b\n```'
        model=Mock(return_value=fixed);reply,checks=review_reply(broken,'Escreva uma função Python para somar',{},model)
        self.assertEqual(reply,fixed);self.assertEqual(checks[0]['status'],'PASS');self.assertEqual(model.call_count,1);self.assertIn('SyntaxError',model.call_args.args[0])
        model=Mock(return_value='```python\nprint(1)\n```');reply,checks=review_reply(broken,'Escreva uma função Python para somar',{},model);self.assertEqual(reply,broken);self.assertEqual(checks[0]['status'],'FAIL')
    def test_no_repair_claim_for_explanation_or_unverified_response(self):
        from mark.coding_assistant import review_reply
        from unittest.mock import Mock
        broken='```python\ndef add(\n```';model=Mock(return_value='Agora está certo')
        reply,checks=review_reply(broken,'Explique um erro Python',{},model);model.assert_not_called();self.assertEqual(reply,broken)
        reply,checks=review_reply(broken,'Crie uma função Python',{},model);self.assertEqual(reply,broken)

if __name__=='__main__':unittest.main()
