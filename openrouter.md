# OpenRouter Quickstart 정리 (API 사용 중심)

이 문서는 OpenRouter Quickstart 페이지 내용을 바탕으로, **API 사용 방법**을 중심으로 정리한 것이다.

* 공식 문서: [https://openrouter.ai/docs/quickstart](https://openrouter.ai/docs/quickstart)
* 초점: 직접 HTTP 호출, TypeScript/Python/OpenAI SDK 활용법, 필수/선택 헤더, 엔드포인트 구조 등

---

## 1. OpenRouter 개요

* OpenRouter는 **여러 AI 모델을 하나의 통합 API**로 제공하는 서비스다.
* 하나의 엔드포인트를 통해 OpenAI, Anthropic, 기타 오픈 모델 등 **수백 개의 모델**에 접근할 수 있다.
* 특징:

  * 통합된 `chat/completions` 스타일 API
  * fallback 처리, 비용 대비 효율적인 모델 선택 등을 자동으로 지원
  * 모델/프로바이더 라우팅, 프롬프트 캐싱, 툴 호출, 웹 검색 등 다양한 기능을 추가로 제공

이 문서에서는 구체적인 기능보다는 **가장 기본적인 요청(챗 컴플리션)**과 사용 패턴에 초점을 둔다.

---

## 2. API Key 및 공통 개념

### 2.1 API Key

* 모든 예제에서 `<OPENROUTER_API_KEY>` 자리에는 발급받은 OpenRouter API 키를 넣는다.
* 이 키는 HTTP 요청의 `Authorization` 헤더에 **Bearer 토큰** 형태로 포함된다.

```http
Authorization: Bearer <OPENROUTER_API_KEY>
```

### 2.2 선택 헤더 (랭킹 및 Attribution)

문서에서 반복해서 등장하는 선택 헤더는 다음 두 가지다.

* `HTTP-Referer`:

  * **사이트 URL**을 넣는다.
  * OpenRouter 리더보드/랭킹에 앱을 표시할 때 사용된다.
* `X-Title`:

  * **앱/사이트 이름**을 넣는다.
  * 마찬가지로 리더보드 상에 표시되는 이름이다.

이 둘은 API 사용에 필수는 아니지만, 추후 통계/랭킹에 노출하고 싶다면 세팅하는 것이 좋다.

예시:

```http
HTTP-Referer: https://your-app.example
X-Title: Your App Name
```

---

## 3. OpenRouter SDK 사용 (TypeScript, Beta)

### 3.1 설치

문서 기준 기본 예시는 **npm**을 사용한다.

```bash
npm install @openrouter/sdk
```

(yarn, pnpm도 사용 가능하지만 예제는 npm 기준.)

### 3.2 기본 사용 예시 (TypeScript)

```ts
import { OpenRouter } from '@openrouter/sdk';

const openRouter = new OpenRouter({
  apiKey: '<OPENROUTER_API_KEY>',
  defaultHeaders: {
    'HTTP-Referer': '<YOUR_SITE_URL>', // 선택: 리더보드에 표시할 사이트 URL
    'X-Title': '<YOUR_SITE_NAME>',     // 선택: 리더보드에 표시할 앱 이름
  },
});

const completion = await openRouter.chat.send({
  model: 'openai/gpt-4o',
  messages: [
    {
      role: 'user',
      content: 'What is the meaning of life?',
    },
  ],
  stream: false,
});

console.log(completion.choices[0].message.content);
```

#### 설명

* `OpenRouter` 인스턴스를 생성할 때:

  * `apiKey`에 OpenRouter API 키를 설정한다.
  * `defaultHeaders`에 선택적인 랭킹용 헤더를 지정한다.
* `openRouter.chat.send` 메서드:

  * `model`: 사용할 모델 ID (예: `openai/gpt-4o`)
  * `messages`: OpenAI 스타일의 role 기반 메시지 배열
  * `stream`: `false`이면 전체 응답을 한 번에 받고, `true`이면 스트리밍 (SDK에서 지원할 경우)
* 응답 구조는 OpenAI ChatCompletion과 유사하게 `choices[0].message.content` 등에 텍스트가 들어있다.

---

## 4. OpenRouter API를 직접 호출하기 (HTTP / Python 예시)

OpenRouter는 **표준 REST 스타일**로 직접 호출할 수 있다.

### 4.1 기본 엔드포인트

* Chat Completions 엔드포인트:

```text
https://openrouter.ai/api/v1/chat/completions
```

### 4.2 Python (requests) 예제

```python
import requests
import json

response = requests.post(
    url="https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": "Bearer <OPENROUTER_API_KEY>",
        "HTTP-Referer": "<YOUR_SITE_URL>",  # 선택: 리더보드용 URL
        "X-Title": "<YOUR_SITE_NAME>",      # 선택: 리더보드용 이름
    },
    data=json.dumps({
        "model": "openai/gpt-4o",  # 모델 지정 (생략 시 라우팅 기본값 사용 가능)
        "messages": [
            {
                "role": "user",
                "content": "What is the meaning of life?"
            }
        ]
    })
)

print(response.status_code)
print(response.json())
```

#### 요청 포인트 정리

* `POST` 메서드 사용.
* `headers`:

  * `Authorization` (필수)
  * `HTTP-Referer`, `X-Title` (선택)
* `body` (JSON 직렬화):

  * `model`: 사용할 모델 이름 문자열 (예: `openai/gpt-4o`, `anthropic/claude-3-sonnet` 등)
  * `messages`: OpenAI Chat API와 동일한 구조의 메시지 배열
* 응답은 `response.json()`으로 파싱하면 OpenAI ChatCompletion 스타일 JSON이 나온다.

### 4.3 인터랙티브 Request Builder

* 문서에서는 **Request Builder**라는 도구를 제공한다.
* 웹 UI에서 모델, 메시지, 헤더 등을 선택하면

  * Python, TypeScript, Shell 등 다양한 언어로 API 요청 코드를 자동으로 생성해 준다.
* 복잡한 옵션을 사용하는 경우 이 빌더를 활용하면 편하다.

---

## 5. OpenAI SDK를 통한 사용

OpenRouter는 **OpenAI SDK와 호환되는 인터페이스**를 제공한다.
즉, OpenAI 클라이언트를 쓰되 `baseURL`만 OpenRouter로 바꾸고, 키를 OpenRouter 키로 설정하면 된다.

### 5.1 TypeScript 예제 (openai npm 패키지)

```ts
import OpenAI from 'openai';

const openai = new OpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: '<OPENROUTER_API_KEY>',
  defaultHeaders: {
    'HTTP-Referer': '<YOUR_SITE_URL>', // 선택: 리더보드용 URL
    'X-Title': '<YOUR_SITE_NAME>',     // 선택: 리더보드용 이름
  },
});

async function main() {
  const completion = await openai.chat.completions.create({
    model: 'openai/gpt-4o',
    messages: [
      {
        role: 'user',
        content: 'What is the meaning of life?',
      },
    ],
  });

  console.log(completion.choices[0].message);
}

main();
```

#### 핵심 포인트

* `baseURL`을 `https://openrouter.ai/api/v1`로 지정해야 한다.
* `apiKey`는 OpenAI 키가 아니라 **OpenRouter API 키**.
* 나머지 사용법(`chat.completions.create`, `messages` 구조 등)은 OpenAI 공식 SDK와 동일하다.
* 스트리밍도 지원한다고 명시되어 있으며, OpenAI SDK의 스트리밍 방식과 거의 동일하게 사용할 수 있다.

### 5.2 Python (OpenAI SDK)도 유사

* 동일하게 `base_url`(또는 `baseURL`)을 OpenRouter로 지정하고,
* `api_key`에 OpenRouter 키를 넣으면,
* OpenAI 스타일의 ChatCompletion 호출 코드를 거의 그대로 재사용할 수 있다.

(문서에는 TypeScript 예제가 주로 나와 있고, Python 예제는 API Reference/다른 섹션에 있다.)

---

## 6. 기타: Third-party SDK / Framework 연동

Quickstart에서는 자세한 코드 대신 요약만 제시된다.

* LangChain, Vercel AI SDK, Effect AI SDK, PydanticAI, Zapier, LiveKit 등 다양한 프레임워크/SDK와의 연동이 가능하다.
* 이에 대한 자세한 예제는 **Frameworks and Integrations Overview** 문서로 링크가 제공된다.

Use case 예:

* LangChain에서 LLM backend를 OpenRouter로 바꾸기
* Vercel AI SDK에서 provider를 OpenRouter로 설정
* 기타 Observability 도구(예: Langfuse, Arize)와 연동 등

---

## 7. 요약: 최소한으로 알아야 할 것

1. **기본 엔드포인트**

   * `https://openrouter.ai/api/v1/chat/completions` (Chat)

2. **필수 헤더**

   * `Authorization: Bearer <OPENROUTER_API_KEY>`

3. **선택 헤더 (랭킹/Attribution)**

   * `HTTP-Referer`: 앱/사이트 URL
   * `X-Title`: 앱/사이트 이름

4. **요청 Body 형식** (OpenAI ChatCompletion 스타일)

   ```json
   {
     "model": "openai/gpt-4o",
     "messages": [
       {
         "role": "user",
         "content": "Your prompt here"
       }
     ]
   }
   ```

5. **OpenAI SDK를 그대로 재사용 가능**

   * `baseURL`만 `https://openrouter.ai/api/v1`로 바꾸고
   * `apiKey`에 OpenRouter 키를 넣으면 된다.

6. **다양한 SDK/프레임워크용 예제**는 Request Builder와 Frameworks 문서에서 제공.

이 정도만 이해하면, OpenRouter를 이용해 **OpenAI 호환 스타일의 ChatCompletion API를 바로 호출**할 수 있다.
