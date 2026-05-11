package com.myspace.gangnam324;

import org.kie.api.runtime.process.WorkItem;
import org.kie.api.runtime.process.WorkItemHandler;
import org.kie.api.runtime.process.WorkItemManager;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 외부 라이브러리(Jena) 없이 순수 JDK만으로 SPARQL을 실행하는 핸들러
 * HTTP GET 요청을 사용하여 Fuseki와 통신합니다.
 */
public class HttpSparqlHandler implements WorkItemHandler {

    // Fuseki 기본 주소 (변경 가능)
    private String defaultEndpoint = "http://localhost:3030/fc/query";

    public HttpSparqlHandler() {
    }

    @Override
    public void executeWorkItem(WorkItem workItem, WorkItemManager manager) {
        
        // 1. 파라미터 수신
        String queryString = (String) workItem.getParameter("Query");
        String endpoint = (String) workItem.getParameter("Endpoint");
        
        if (endpoint == null || endpoint.isEmpty()) {
            endpoint = defaultEndpoint;
        }

        System.out.println("[HttpSparql] Query: " + queryString);

        Map<String, Object> results = new HashMap<>();
        
        try {
            // 2. URL 인코딩 (쿼리에 공백이나 특수문자가 있으므로 필수)
            String encodedQuery = URLEncoder.encode(queryString, "UTF-8");
            String requestUrl = endpoint + "?query=" + encodedQuery;

            // 3. HTTP 연결 설정 (GET 방식)
            URL url = new URL(requestUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            // JSON 포맷으로 결과를 달라고 요청
            conn.setRequestProperty("Accept", "application/sparql-results+json"); 

            int responseCode = conn.getResponseCode();
            if (responseCode == 200) {
                // 4. 응답 읽기
                BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                String inputLine;
                StringBuffer response = new StringBuffer();
                while ((inputLine = in.readLine()) != null) {
                    response.append(inputLine);
                }
                in.close();

                // 5. 결과 파싱 (JSON 라이브러리 없이 문자열 파싱)
                // Fuseki 응답 예: { ... "results": { "bindings": [ { "val": { "type": "literal", "value": "30" } } ] } }
                String jsonResponse = response.toString();
                String parseResult = extractValueFromJson(jsonResponse);

                System.out.println("[HttpSparql] Parsed Result: " + parseResult);
                results.put("Result", parseResult);
                
            } else {
                System.err.println("[HttpSparql] HTTP Error: " + responseCode);
            }

        } catch (Exception e) {
            e.printStackTrace();
        }

        // 6. 프로세스 진행
        manager.completeWorkItem(workItem.getId(), results);
    }

    @Override
    public void abortWorkItem(WorkItem workItem, WorkItemManager manager) {
        // 비동기 취소 시 로직
    }

    /**
     * 무거운 JSON 라이브러리 의존성을 피하기 위해
     * 정규식(Regex)으로 첫 번째 "value" 값을 추출하는 헬퍼 메서드
     */
    private String extractValueFromJson(String json) {
        try {
            // 패턴: "value" : "찾을값"  형태를 찾음
            // 주의: 이 방식은 결과가 1개일 때(단일 추론 결과) 유효함
            Pattern pattern = Pattern.compile("\"value\"\\s*:\\s*\"(.*?)\"");
            Matcher matcher = pattern.matcher(json);
            
            if (matcher.find()) {
                return matcher.group(1); // 첫 번째 매칭된 그룹 반환
            }
        } catch (Exception e) {
            System.err.println("[HttpSparql] Parsing Error");
        }
        return null; // 결과 없음
    }
}
