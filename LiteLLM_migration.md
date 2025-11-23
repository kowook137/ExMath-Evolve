LiteLLM 기반으로 DeepEvolve 파이프라인을 이식하기 위한 상세 가이드입니다. 현재 구조(Researcher → Coder → Debugger → Evaluator 등)를 유지하면서 다양한 LLM 공급자(OpenRouter 포함)를 사용 가능하도록 만드는 것을 목표로 합니다.  
이 문서는 **프록시 방식**과 **직접 API 방식** 모두를 다루며, 각 방식별로 필요한 설정, 코드 변경 범위, 운영 팁을 상세히 기술합니다.

---

## 1. LiteLLM 이해 및 준비

1. **LiteLLM 소개**
   - 여러 공급자(OpenAI, OpenRouter, Anthropic, DeepSeek, Azure 등)의 모델을 하나의 Python 인터페이스 혹은 프록시 서버로 묶어주는 오픈소스 라이브러리.
   - 설치: `pip install litellm`

2. **현 파이프라인의 특징**
   - OpenAI Agents SDK를 이용해 에이전트를 orchestration.
   - HTTP 요청은 SDK가 `api.openai.com`으로 직접 전송 → 다른 공급자를 쓰려면 SDK 호출부를 재구성해야 함.

3. **이식 전략**
   - **전략 A**: LiteLLM Python API로 에이전트 로직을 전면 재작성(대규모 리팩터링)
   - **전략 B**: LiteLLM Proxy 서버를 띄워 OpenAI 호환 인터페이스를 흉내 내고, 기존 SDK를 그 프록시에 연결(구조 유지)
   - 초기에는 전략 B를 권장, 필요 시 단계적으로 전략 A로 이행.

---

## 2. LiteLLM Proxy 방식 도입(권장)

1. **설치**
   ```bash
   pip install litellm
   ```
   + `lite_config.yaml` 파일에 사용할 모델 및 API 키 정보를 정의.

2. **Proxy 서버 실행**
   ```bash
   litellm --port 4000 --config lite_config.yaml
   ```
   + 프록시는 OpenAI와 동일한 REST 인터페이스 제공.
   + LiteLLM 설정 예시는 아래 참고.

3. **환경 변수 조정**
   ```bash
   export OPENAI_API_BASE="http://localhost:4000"
   export OPENAI_API_KEY="dummy-key"  # 프록시에서 키 검증을 끄거나 별도 키 사용
   ```
   + 필요 시 `OPENAI_API_TYPE`, `OPENAI_ORGANIZATION` 등도 프록시 환경에 맞게 조정.
   + 또는 `configs/config.yaml`의 `api` 섹션을 다음처럼 설정하여 자동으로 환경 변수를 세팅할 수 있음:
     ```yaml
     api:
       key_env: "OPENROUTER_API_KEY"
       base_url: "http://localhost:4000/v1"
     ```
     위와 같이 설정하면 DeepEvolve 실행 시 `OPENROUTER_API_KEY` 값을 읽어 `OPENAI_API_KEY`에 주입하고, 엔드포인트도 프록시 URL로 자동 지정된다.

4. **모델 이름 매핑**
   + `lite_config.yaml`의 `model_name`을 파이프라인에서 사용하는 이름(`gpt-4o-mini`, `o3-mini` 등)과 일치시키면 SDK 측 변경 없이 프록시가 해당 모델을 적절한 공급자로 라우팅.

5. **실행**
   + 기존과 동일하게 `python deepevolve.py ...` 실행.
   + SDK → LiteLLM 프록시 → 실제 공급자 순으로 트래픽이 흐름.

6. **전체 흐름 요약**
   1. LiteLLM 프록시 서버 실행(모델 리스트/폴백 지정)
   2. DeepEvolve 실행 전 환경 변수로 `OPENAI_API_BASE=http://localhost:4000` 설정
   3. 모든 에이전트가 프록시를 통해 원하는 공급자 모델을 사용

### 레포 내 제공 도구

- `lite_config.example.yaml`: 프록시 설정 템플릿. `cp lite_config.example.yaml lite_config.yaml` 후 각 API 키 환경 변수를 채우면 된다.
- `scripts/start_litellm_proxy.sh`: `./scripts/start_litellm_proxy.sh lite_config.yaml` 처럼 실행하면 LiteLLM 프록시가 (기본 `4000` 포트) 구동되며 `OPENAI_API_BASE`/`OPENAI_API_KEY` 환경 변수가 세션에 자동 설정된다. `LITELLM_PROXY_PORT`∙`LITELLM_PROXY_ARGS` 환경 변수로 포트나 추가 CLI 옵션을 커스텀할 수 있다.

### lite_config.yaml 예시
```yaml
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: ${OPENAI_API_KEY}
  - model_name: openrouter/mistral-large
    litellm_params:
      model: openrouter/mistral-large
      api_key: ${OPENROUTER_API_KEY}
      api_base: https://openrouter.ai/api/v1
  - model_name: deepseek/chat
    litellm_params:
      model: deepseek/chat
      api_key: ${DEEPSEEK_API_KEY}

fallbacks:
  gpt-4o-mini:
    - openrouter/mistral-large
    - deepseek/chat
```

---

## 3. LiteLLM Python API 직접 사용(선택)

프록시 대신 코드 내부에서 LiteLLM 함수를 호출하려면 다음 단계가 필요합니다.

1. **에이전트 로직 재작성**
   - `ResearcherAgent`, `CoderAgent`, `DebuggerAgent` 등에서 OpenAI SDK 대신 LiteLLM API(`litellm.completion`, `litellm.chat_completion`) 활용.
   - OpenAI Agents SDK 전용 객체(Agent, Runner 등)를 대체하거나 추상화 계층을 직접 구현.

2. **Tracing/Tool 재구성**
   - SDK가 제공하는 tracing, tool 호출 로직을 일반 Python 함수 호출/로깅으로 대체.
   - 필요 시 LiteLLM 프록시의 로깅 기능이나 별도 로그 시스템 사용.

3. **모델/키 관리**
   - LiteLLM은 환경 변수에서 각 공급자의 API 키를 읽으므로, 모든 모델에 해당하는 키를 설정해 둬야 함.

4. **테스트**
   - Planner → Researcher → Coder → Debugger → Evaluator 전체 루프가 LiteLLM API로 정상 동작하는지 검증.

이 방식은 가장 유연하나 리팩터링 범위가 큼. 일반적으로는 프록시 방식으로 진입한 뒤 단계적으로 API 호출 방식으로 전환하는 것을 권장.

---

## 4. 운영 체크포인트

| 항목 | 설명 |
|------|------|
| **API 키 관리** | `.env` 또는 환경 변수로 OpenRouter, OpenAI, Anthropic 등 여러 공급자의 키를 관리하고, LiteLLM이 읽도록 설정 |
| **모델 매핑** | 파이프라인에서 사용하는 모든 모델 이름이 LiteLLM 설정에 등록되어 있어야 함 |
| **배포** | 프록시 방식을 사용할 경우 LiteLLM 프록시 서버를 systemd, Docker 등으로 상시 운영 |
| **로그/모니터링** | LiteLLM 프록시 로그 또는 별도 로깅 시스템을 통해 요청/응답 추적 |

---

## 5. 결론

- **단기 목표**: LiteLLM 프록시 서버를 띄워 기존 OpenAI Agents SDK를 그대로 사용하면서 OpenRouter 등 다양한 모델 공급자를 활용.
- **장기 목표**: 필요 시 프록시 환경을 유지한 채 LiteLLM Python API를 직접 호출하도록 리팩터링하여 더 세밀한 제어 확보.

이 과정을 따르면, 파이프라인의 구조와 기능을 유지하면서도 다양한 LLM 공급자를 활용할 수 있는 LiteLLM 기반 환경으로 이식할 수 있습니다.

---

## 부록 A. 타입별 체크리스트

| 구분 | 체크 항목 |
|------|-----------|
| Proxy 도입 전 | LiteLLM 설치, 모델 설정 파일 준비 |
| Proxy 실행 | 포트/모델 목록/폴백 확인, 로그 경로 지정 |
| DeepEvolve 실행 전 | `OPENAI_API_BASE`/`OPENAI_API_KEY` 환경 변수 점검, 기존 키 초기화 |
| 운영 중 | 모델 응답 품질, 폴백 동작 확인, 비용 모니터링 |

## 부록 B. 자주 발생하는 문제와 해결

1. **401 Unauthorized**
   - 원인: 프록시가 공급자 키를 찾지 못했거나, DeepEvolve가 여전히 `api.openai.com`에 요청.
   - 해결: `OPENAI_API_BASE` 확인, `lite_config.yaml`의 `api_key` 설정 점검.
2. **모델 이름 불일치**
   - 원인: DeepEvolve에서 쓰는 모델명이 LiteLLM 설정과 다름.
   - 해결: `model_list`의 `model_name`을 파이프라인 사용 이름과 동일하게 맞춤.
3. **프록시 로깅 부족**
   - 해결: `litellm --verbose` 옵션 사용 또는 별도 로깅 경로 설정.
4. **모델 폴백 미동작**
   - 해결: `fallbacks` 섹션에서 기준 모델 이름을 정확히 기입하고, 폴백 대상이 `model_list`에 존재하는지 확인.
