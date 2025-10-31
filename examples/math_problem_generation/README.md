# Math Problem Generation

이 워크스페이스는 DeepEvolve를 활용해 **LLM이 풀기 어려운 수학 문제를 자동 생성**하고 검증하기 위한 환경을 제공합니다.  
핵심 목표는 다음과 같습니다.

- 시드 아이디어(`info.json`, `initial_idea.json`)를 기반으로 Planner/Searcher/Writer가 문제 설계안을 도출.
- Developer가 심볼릭/수치 검사를 통해 정답의 타당성을 확인.
- Debugger가 LLM을 호출하여 문제 난이도를 측정하고, 쉽게 풀린 경우 Planner에게 피드백을 전달.
- 충분히 어려운 문제는 JSON 포맷으로 데이터베이스에 축적.

초기 코드는 최소한의 구조만을 제공합니다. 추후 단계에서 생성 로직, 자동 검증, 난이도 평가를 점진적으로 강화할 예정입니다.
