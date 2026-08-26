from coding_agent.config import Config
def test_env_config(monkeypatch,tmp_path):
 monkeypatch.setenv('OPENAI_API_KEY','EMPTY');monkeypatch.setenv('OPENAI_MODEL','gemma4-26b');monkeypatch.setenv('OPENAI_BASE_URL','http://localhost:8000/v1')
 c=Config.from_env(str(tmp_path));assert c.base_url.endswith('/v1') and c.model=='gemma4-26b'
def test_price_env(monkeypatch,tmp_path):
 monkeypatch.setenv('CODER_INPUT_PRICE_PER_MILLION','1.5');monkeypatch.setenv('CODER_OUTPUT_PRICE_PER_MILLION','6')
 c=Config.from_env(str(tmp_path));assert c.price_input_per_million==1.5 and c.price_output_per_million==6.0
