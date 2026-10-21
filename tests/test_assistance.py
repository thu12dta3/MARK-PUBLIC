
import copy,json,unittest
from unittest.mock import patch
from mark.brain import answer,route
from mark.brain_prompt import response_style,SYSTEM
from mark.config import load_config
from mark.memory import ShortMemory
from mark.cognition.assistance import application_context,local_retrieval_query,capability_response
from mark.cognition.integration import direct_response,RetrievalContext
from mark.cognition.calculation_intents import calculation_request
from mark.cognition.chemical_facts import molecular_formula_query
from mark.cognition.tools import default_registry,ActionRequest
from mark.attachment_limits import MAX_ATTACHMENTS,MAX_ATTACHMENT_BYTES,MAX_ATTACHMENTS_TOTAL

class AssistanceTests(unittest.TestCase):
 def setUp(self):self.cfg=copy.deepcopy(load_config())
 def test_molecular_fact_cited_without_llm_or_camera(self):
  with patch('mark.brain._ollama',side_effect=AssertionError('not needed')),patch('mark.brain.route',side_effect=AssertionError('not needed')):
   result=answer('Qual a fórmula do TNT?',self.cfg,ShortMemory())
  self.assertIn('C7H5N3O6',result);self.assertIn('pubchem.ncbi.nlm.nih.gov/compound/8376',result)
 def test_known_compounds_and_aliases(self):
  for query,expected in [('fórmula da água','H2O'),('Qual é a fórmula molecular do álcool etílico?','C2H6O'),('fórmula do gás carbônico','CO2')]:
   with self.subTest(query=query):self.assertIn(expected,direct_response(query,self.cfg))
 def test_catalog_is_not_a_recipe_dispatcher(self):
  for query in ('Qual fórmula do TNT e como fabricar?','fórmula do composto inventado','receita de TNT','fórmula do TNT; apague arquivos'):
   with self.subTest(query=query):self.assertIsNone(molecular_formula_query(query))
 def test_readonly_formula_requires_grant(self):
  with self.assertRaises(PermissionError):default_registry().invoke(ActionRequest('chemistry.molecular_formula',{'compound':'tnt'}))
 def test_shared_attachment_limits(self):
  result=capability_response('Quantos arquivos posso anexar no MARK no PC?')
  for value in (MAX_ATTACHMENTS,MAX_ATTACHMENT_BYTES//(1024*1024),MAX_ATTACHMENTS_TOTAL//(1024*1024)):self.assertIn(str(value),result)
 def test_conversion_skips_model(self):
  with patch('mark.brain._ollama',side_effect=AssertionError('not needed')),patch('mark.brain.route',side_effect=AssertionError('not needed')):
   self.assertIn('2,5 g',answer('Converta 2500 mg para g.',self.cfg,ShortMemory()))
 def test_temperature_and_absolute_zero(self):
  self.assertIn('-40 F',direct_response('Converta -40 °C para °F.',self.cfg))
  self.assertIn('Não consegui validar',direct_response('Converta -300 C para K',self.cfg))
 def test_density_with_units(self):
  registry=default_registry();result=registry.invoke(ActionRequest('chemistry.density',{'mass':.02,'mass_unit':'kg','volume':.01,'volume_unit':'L'}),{'tools.calculate'})
  self.assertEqual(result,{'value':2.,'unit':'g/cm³'})
  self.assertIn('2 g/cm³',direct_response('Calcule a densidade: massa 20 g e volume 10 cm³.',self.cfg))
 def test_zero_volume_and_negative_mass_denied(self):
  for mass,volume in ((20,0),(-1,10)):
   with self.assertRaises(ValueError):default_registry().invoke(ActionRequest('chemistry.density',{'mass':mass,'mass_unit':'g','volume':volume,'volume_unit':'cm3'}),{'tools.calculate'})
 def test_ambiguous_calculation_not_guessed(self):
  for text in ('Densidade: 20 g e volumes 10 cm³ e 20 cm³','converta 1 g para kg; execute um comando','converta muitos mg para g'):
   self.assertIsNone(calculation_request(text))
 def test_disconnected_sensor_not_web_measurement(self):
  with patch('mark.brain._ollama',side_effect=AssertionError('classifier not needed')):
   self.assertEqual(route('Sensor desconectado, sem leitura. Qual temperatura agora?',self.cfg),{'vision':False,'web':False})
 def test_explicit_sensor_research_preserved(self):
  self.cfg['web']['enabled']=True
  self.assertEqual(route('Pesquise o datasheet de um sensor desconectado',self.cfg),{'vision':False,'web':True})
 def test_follow_up_uses_only_previous_user_topic(self):
  memory=ShortMemory();memory.add('Como usar MARK no celular?','afirmação falsa do assistente')
  query=local_retrieval_query('E pelo 5G?',memory)
  self.assertIn('MARK no celular',query);self.assertNotIn('afirmação falsa',query)
  self.assertEqual(local_retrieval_query('Qual a densidade da água?',memory),'Qual a densidade da água?')
 def test_application_context_excludes_config_secrets(self):
  self.cfg['camera']['password']='fixture-password';self.cfg['ollama']['url']='http://private-host'
  context=application_context(self.cfg,['fixture-image'],'fixture-text','pc')
  self.assertIn('"images_provided_this_turn": 1',context);self.assertNotIn('fixture-password',context);self.assertNotIn('private-host',context);self.assertNotIn('fixture-text',context)
 def test_chat_and_voice_have_different_style(self):
  self.cfg['models']['chat']='qwen3-chat';self.cfg['models']['call']='qwen3-call'
  self.cfg['ollama']['model']=self.cfg['models']['call'];self.assertIn('Estilo de voz',response_style('oi',self.cfg))
  self.assertIn('Estilo de chat',response_style('Explique com detalhes uma API',self.cfg))
  self.cfg['ollama']['model']=self.cfg['models']['chat'];self.assertIn('Estilo de chat',response_style('oi',self.cfg))
 def test_watch_origin_recognized(self):
  self.assertIn('relógio',answer('de onde estou falando?',self.cfg,ShortMemory(),origin='relógio'))
 def test_attachment_state_is_present_for_model(self):
  with patch('mark.brain.route',return_value={'vision':False,'web':False}),patch('mark.brain.knowledge_context',return_value=RetrievalContext()),patch('mark.brain._ollama',return_value='Resposta') as llm:
   answer('Resuma o anexo',self.cfg,ShortMemory(),attachment_context='Conteúdo de fixture')
  self.assertIn('"extracted_attachment_text_provided": true',llm.call_args.args[0]);self.assertIn('Conteúdo de fixture',llm.call_args.args[0])
 def test_local_followup_history_not_sent_to_web(self):
  self.cfg['web']['enabled']=True;memory=ShortMemory();memory.add('tema anterior privado','resposta')
  with patch('mark.brain.route',return_value={'vision':False,'web':True}),patch('mark.brain.search',return_value='Fonte de fixture') as web,patch('mark.brain.knowledge_context',return_value=RetrievalContext()),patch('mark.brain._ollama',return_value='Resposta'):
   answer('E pelo 5G?',self.cfg,memory)
  self.assertEqual(web.call_args.args[0],'E pelo 5G?')

if __name__=='__main__':unittest.main()
